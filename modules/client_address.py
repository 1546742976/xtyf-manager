"""Resolve a rate-limit source, without trusting forwarded host or protocol."""
import ipaddress

from flask import current_app, g, request


def parse_trusted_proxies(value):
    """Validate once at startup. Empty configuration deliberately trusts nobody."""
    try:
        if not value.strip():
            return ()
        networks = tuple(ipaddress.ip_network(part.strip(), strict=True) for part in value.split(','))
        for version in (4, 6):
            collapsed = ipaddress.collapse_addresses(n for n in networks if n.version == version)
            if any(n.prefixlen == 0 for n in collapsed):
                raise ValueError()
        # Mapped peers are normalized to IPv4; trust must use an IPv4 CIDR too.
        if any(n.version == 6 and n.network_address.ipv4_mapped for n in networks):
            raise ValueError()
        return networks
    except (ValueError, TypeError, AttributeError):
        raise ValueError('TRUSTED_PROXY_CIDRS 无效：使用逗号分隔的准确代理 CIDR，不允许全网信任或 IPv4 映射网段') from None


def _address(value):
    if '%' in value:
        raise ValueError('不接受带区域标识的地址')
    address = ipaddress.ip_address(value)
    return getattr(address, 'ipv4_mapped', None) or address


def client_address():
    """Rightmost untrusted hop wins; malformed forwarding always uses the peer."""
    if hasattr(g, '_client_address'):
        return g._client_address
    peer = request.remote_addr or ''
    resolved = peer
    networks = current_app.config.get('TRUSTED_PROXY_NETWORKS', ())
    try:
        address = _address(peer)
        resolved = str(address)
        def trusted(candidate):
            return any(candidate in network for network in networks)
        if trusted(address):
            header = request.headers.get('X-Forwarded-For', '')
            parts = header.split(',')
            if not header or len(header) > 2048 or len(parts) > 16:
                raise ValueError('转发链长度无效')
            # Validate the entire chain, including attacker-supplied left hops.
            hops = [_address(part.strip()) for part in parts]
            for hop in reversed(hops):
                if not trusted(hop):
                    resolved = str(hop)
                    break
    except ValueError:
        pass
    g._client_address = resolved
    return resolved
