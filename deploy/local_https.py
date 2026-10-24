"""Local HTTPS front door, optionally starting the HTTP app in this process.

Trust root-ca.cer on the phone before opening the printed HTTPS address.
This tool never installs trust. Only --with-app loads and starts the application.
"""

from __future__ import annotations

import argparse
import csv
import importlib
import ipaddress
import os
from pathlib import Path
import ssl
import subprocess
import sys
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit, urlunsplit

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from werkzeug.middleware.http_proxy import ProxyMiddleware
from werkzeug.serving import make_server, run_simple
from werkzeug.wrappers import Response


ROOT = Path(__file__).resolve().parents[1]
LAN_NETWORKS = tuple(ipaddress.ip_network(value) for value in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
))
CERTIFICATE_PATH = "/local-https/root-ca.cer"


def lan_ip(value: str) -> ipaddress.IPv4Address:
    try:
        address = ipaddress.IPv4Address(value)
    except ipaddress.AddressValueError as exc:
        raise argparse.ArgumentTypeError("--ip must be a private LAN IPv4 address") from exc
    if not any(address in network for network in LAN_NETWORKS):
        raise argparse.ArgumentTypeError("--ip must be in an RFC 1918 private LAN range")
    return address


def port_number(value: str) -> int:
    try:
        port = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("port must be an integer") from exc
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return port


def detect_lan_ip() -> ipaddress.IPv4Address:
    if os.name != "nt":
        raise ValueError("Automatic LAN IP detection is Windows-only; pass --ip explicitly.")
    # Physical adapters with a default gateway exclude tunnel/VPN interfaces.
    # PowerShell emits only IPv4 text; no locale-dependent table parsing.
    script = r"""
$ErrorActionPreference = 'Stop'
Get-NetAdapter -Physical | Where-Object { $_.Status -eq 'Up' } | ForEach-Object {
    $adapter = $_
    $configuration = Get-NetIPConfiguration -InterfaceIndex $adapter.ifIndex
    if ($configuration.IPv4DefaultGateway) {
        $metric = (Get-NetIPInterface -InterfaceIndex $adapter.ifIndex -AddressFamily IPv4 |
            Measure-Object -Property InterfaceMetric -Minimum).Minimum
        $configuration.IPv4Address | ForEach-Object {
            [PSCustomObject]@{ Address = $_.IPAddress; Metric = $metric }
        }
    }
} | Sort-Object Metric, Address | Select-Object -ExpandProperty Address
"""
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, check=True, timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    for value in result.stdout.splitlines():
        try:
            return lan_ip(value.strip())
        except argparse.ArgumentTypeError:
            continue
    raise ValueError("No active physical LAN adapter with an IPv4 gateway was found; pass --ip explicitly.")


def protect_directory(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name == "nt":
        # Windows chmod does not restrict NTFS read access. Restrict inherited
        # permissions before creating keys; never change a trust store.
        identity = subprocess.check_output(
            ["whoami", "/user", "/fo", "csv", "/nh"], text=True,
        )
        sid = next(csv.reader(identity.strip().splitlines()))[1]
        subprocess.run(
            ["icacls", str(directory), "/inheritance:r", "/grant:r", f"*{sid}:(OI)(CI)F"],
            check=True, capture_output=True,
        )
    else:
        directory.chmod(0o700)


def write_file(path: Path, data: bytes) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def private_bytes(key: rsa.RSAPrivateKey) -> bytes:
    return key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def key_matches(key: rsa.RSAPrivateKey, certificate: x509.Certificate) -> bool:
    return key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo,
    ) == certificate.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def prepare_certificates(directory: Path, address: ipaddress.IPv4Address):
    protect_directory(directory)
    now = datetime.now(timezone.utc)
    ca_key_path, ca_path = directory / "root-ca.key", directory / "root-ca.pem"
    if ca_key_path.exists() != ca_path.exists():
        raise ValueError("Incomplete local CA. Use a new --cert-dir and trust its root-ca.cer.")
    if ca_path.exists():
        ca_key = serialization.load_pem_private_key(ca_key_path.read_bytes(), password=None)
        ca = x509.load_pem_x509_certificate(ca_path.read_bytes())
        if not isinstance(ca_key, rsa.RSAPrivateKey) or not key_matches(ca_key, ca):
            raise ValueError("The saved local CA key does not match its certificate.")
        if not (ca.not_valid_before_utc <= now < ca.not_valid_after_utc - timedelta(days=30)):
            raise ValueError("The local CA needs renewal. Use a new --cert-dir and trust its root-ca.cer.")
    else:
        ca_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "XTYF Local Development CA")])
        ca = (
            x509.CertificateBuilder()
            .subject_name(name).issuer_name(name).public_key(ca_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5))
            .not_valid_after(now + timedelta(days=1095))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.KeyUsage(
                digital_signature=False, content_commitment=False, key_encipherment=False,
                data_encipherment=False, key_agreement=False, key_cert_sign=True,
                crl_sign=True, encipher_only=False, decipher_only=False,
            ), critical=True)
            .add_extension(x509.NameConstraints(
                permitted_subtrees=[x509.IPAddress(network) for network in LAN_NETWORKS]
                + [x509.DNSName("localhost")], excluded_subtrees=None,
            ), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
            .sign(ca_key, hashes.SHA256())
        )
        write_file(ca_key_path, private_bytes(ca_key))
        write_file(ca_path, ca.public_bytes(serialization.Encoding.PEM))

    public_ca_path = directory / "root-ca.cer"
    write_file(public_ca_path, ca.public_bytes(serialization.Encoding.DER))
    server_path, server_key_path = directory / "server.pem", directory / "server.key"
    reuse_server = False
    if server_path.exists() and server_key_path.exists():
        try:
            server = x509.load_pem_x509_certificate(server_path.read_bytes())
            server_key = serialization.load_pem_private_key(server_key_path.read_bytes(), password=None)
            ca.public_key().verify(
                server.signature, server.tbs_certificate_bytes,
                padding.PKCS1v15(), server.signature_hash_algorithm,
            )
            addresses = server.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
            reuse_server = (
                isinstance(server_key, rsa.RSAPrivateKey) and key_matches(server_key, server)
                and server.issuer == ca.subject
                and server.subject == x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
                and list(addresses) == [x509.IPAddress(address)]
                and server.not_valid_before_utc <= now
                and server.not_valid_after_utc > now + timedelta(days=7)
            )
        except (ValueError, TypeError, InvalidSignature, x509.ExtensionNotFound):
            reuse_server = False
    if not reuse_server:
        server_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        server = (
            x509.CertificateBuilder()
            # Some validators also apply DNS name constraints to the legacy
            # commonName. The actual LAN identity is the IP SAN below.
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]))
            .issuer_name(ca.subject).public_key(server_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5))
            .not_valid_after(min(now + timedelta(days=180), ca.not_valid_after_utc))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(address)]), critical=False)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.KeyUsage(
                digital_signature=True, content_commitment=False, key_encipherment=True,
                data_encipherment=False, key_agreement=False, key_cert_sign=False,
                crl_sign=False, encipher_only=False, decipher_only=False,
            ), critical=True)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
            .sign(ca_key, hashes.SHA256())
        )
        write_file(server_key_path, private_bytes(server_key))
        write_file(server_path, server.public_bytes(serialization.Encoding.PEM))
    fingerprint = ":".join(f"{byte:02X}" for byte in ca.fingerprint(hashes.SHA256()))
    return server_path, server_key_path, public_ca_path, fingerprint


def make_proxy(address: str, port: int, upstream_port: int, public_ca: Path):
    authority = address if port == 443 else f"{address}:{port}"
    fallback = Response("Not found", status=404)
    proxy = ProxyMiddleware(fallback, {"/": {
        "target": f"http://127.0.0.1:{upstream_port}", "host": authority,
    }}, timeout=30)
    # Werkzeug normalizes a target prefix '/' to '//'; fix its root mapping.
    proxy.targets["/"] = proxy.targets.pop("//")
    local_authorities = {authority, f"127.0.0.1:{upstream_port}", f"localhost:{upstream_port}"}

    def application(environ, start_response):
        if environ.get("HTTP_HOST") not in {authority, f"{address}:{port}"}:
            return Response("Unexpected Host header", status=400)(environ, start_response)
        if environ["PATH_INFO"] == CERTIFICATE_PATH:
            return Response(
                public_ca.read_bytes(), mimetype="application/pkix-cert",
                headers={"Content-Disposition": 'attachment; filename="root-ca.cer"',
                         "Cache-Control": "no-store"},
            )(environ, start_response)
        for name in tuple(environ):
            if name.startswith("HTTP_X_FORWARDED_") or name == "HTTP_FORWARDED":
                environ.pop(name)
        environ["HTTP_X_FORWARDED_PROTO"] = "https"
        environ["HTTP_X_FORWARDED_HOST"] = authority
        environ["HTTP_X_FORWARDED_PORT"] = str(port)
        environ["HTTP_X_FORWARDED_FOR"] = environ.get("REMOTE_ADDR", "")

        def external_response(status, headers, exc_info=None):
            adjusted = []
            for name, value in headers:
                if name.lower() == "location":
                    location = urlsplit(value)
                    if location.netloc in local_authorities and location.scheme in {"", "http", "https"}:
                        value = urlunsplit(("https", authority, location.path, location.query, location.fragment))
                adjusted.append((name, value))
            return start_response(status, adjusted, exc_info)

        return proxy(environ, external_response)

    return application


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ip", type=lan_ip, help="Private LAN IPv4 (default: detect active Windows physical adapter)")
    parser.add_argument("--port", type=port_number, default=8521, help="HTTPS port (default: 8521)")
    parser.add_argument("--upstream-port", type=port_number, default=os.environ.get("PORT", "8520"),
                        help="HTTP port (default: PORT environment variable or 8520)")
    parser.add_argument("--cert-dir", type=Path, default=ROOT / ".build-tools" / "local-https")
    parser.add_argument("--prepare-only", action="store_true", help="Prepare certificates without starting a listener")
    parser.add_argument("--with-app", action="store_true", help="Start the HTTP app and HTTPS proxy in one foreground process")
    args = parser.parse_args()
    if args.port == args.upstream_port:
        parser.error("HTTPS and upstream ports must differ")
    if args.ip is None:
        try:
            args.ip = detect_lan_ip()
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            parser.exit(1, f"LAN IP detection failed: {exc}\nPass --ip with this computer's private LAN IPv4 address.\n")
    try:
        certificate, key, public_ca, fingerprint = prepare_certificates(args.cert_dir.resolve(), args.ip)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"Certificate preparation failed: {exc}\n")
    authority = str(args.ip) if args.port == 443 else f"{args.ip}:{args.port}"
    print(f"Phone URL: https://{authority}/assets", flush=True)
    print(f"Public CA certificate: {public_ca}", flush=True)
    print(f"CA SHA-256 fingerprint: {fingerprint}", flush=True)
    print("Transfer ONLY root-ca.cer to the phone and trust it before opening the URL.", flush=True)
    print("Keep all .key files private. No trust stores or firewall settings were changed.", flush=True)
    if args.prepare_only:
        return
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(str(certificate), str(key))
    print(f"Forwarding HTTPS to http://127.0.0.1:{args.upstream_port}; Ctrl+C stops this foreground process.", flush=True)
    http_server = None
    http_thread = None
    try:
        if args.with_app:
            sys.path.insert(0, str(ROOT))
            application = importlib.import_module("app")
            http_server = make_server("0.0.0.0", args.upstream_port, application.app, threaded=True)
            http_thread = threading.Thread(target=http_server.serve_forever, name="local-http", daemon=True)
            http_thread.start()
            application.start_invoice_worker()
            print(f"Local HTTP app: http://127.0.0.1:{args.upstream_port}", flush=True)
        run_simple(
            str(args.ip), args.port, make_proxy(str(args.ip), args.port, args.upstream_port, public_ca),
            ssl_context=context, use_reloader=False, use_debugger=False, threaded=True,
        )
    finally:
        if http_server is not None:
            if http_thread is not None and http_thread.is_alive():
                http_server.shutdown()
                http_thread.join(timeout=5)
            http_server.server_close()


if __name__ == "__main__":
    main()
