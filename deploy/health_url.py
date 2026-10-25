"""Shared preflight for the two existing deployment health-check entries."""
import ipaddress
import re
import sys
import unicodedata
from urllib.parse import urlsplit


def validate_health_url(raw):
    message = '健康检查必须是不含凭据、片段或空白的有效 HTTP/HTTPS 地址（端口 1–65535）'
    try:
        if (not isinstance(raw, str) or not raw or len(raw) > 2048 or '#' in raw or '\\' in raw
                or any(c.isspace() or unicodedata.category(c) == 'Cc' for c in raw)):
            raise ValueError()
        url = urlsplit(raw)
        if (url.scheme not in ('http', 'https') or not url.hostname or url.username is not None
                or url.password is not None or url.netloc.endswith(':')
                or (url.port is not None and not 1 <= url.port <= 65535)):
            raise ValueError()
        host = url.hostname
        if '%' in host:
            raise ValueError()
        try:
            ipaddress.ip_address(host)
        except ValueError:
            if ':' in host or re.fullmatch(r'[0-9.]+', host):
                raise ValueError()
            ascii_host = host.encode('idna').decode('ascii').removesuffix('.')
            if len(ascii_host) > 253 or any(not re.fullmatch(r'[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?', label) for label in ascii_host.split('.')):
                raise ValueError()
    except (ValueError, UnicodeError) as exc:
        raise ValueError(message) from exc
    return raw


if __name__ == '__main__':
    try:
        validate_health_url(sys.argv[1] if len(sys.argv) == 2 else '')
    except ValueError as exc:
        sys.exit(str(exc))
