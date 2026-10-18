"""Public, signed UI artifacts. No business data or native credentials are served."""
import base64
import hashlib
import json
from pathlib import Path
import re

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from flask import Blueprint, current_app, jsonify, send_file

ROOT = Path(__file__).resolve().parents[1]
MAX_METADATA = 256 * 1024
MAX_ZIP = 128 * 1024 * 1024
MAX_UNPACKED = 256 * 1024 * 1024
MAX_FILES = 4096
HEX = re.compile(r"[0-9a-f]{64}\Z")
VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+\Z")
PLATFORMS = {"windows-x86_64", "ubuntu-aarch64", "fedora-x86_64", "android"}
bp = Blueprint("client_updates", __name__, url_prefix="/api/client")


def positive_int(value, limit):
    return type(value) is int and 0 < value <= limit


def local_path(value):
    return (isinstance(value, str) and value.startswith("/") and not value.startswith("//")
            and not any(c in value for c in ("\\", "#", "\r", "\n", "\0"))
            and not any(p in {".", ".."} for p in value.split("/")))


def validate_payload(data):
    if not isinstance(data, dict) or data.get("format") != 1 or not positive_int(data.get("sequence"), 2**53 - 1):
        raise ValueError("Invalid UI release sequence or format")
    bundle = data.get("bundle")
    if not isinstance(bundle, dict):
        raise ValueError("Missing UI bundle")
    for name in ("id", "sha256"):
        if not isinstance(bundle.get(name), str) or not HEX.fullmatch(bundle[name]):
            raise ValueError("Invalid UI bundle digest")
    if bundle.get("url") != f'/api/client/ui/{bundle["id"]}.zip':
        raise ValueError("Invalid UI bundle URL")
    for name, limit in (("size", MAX_ZIP), ("unpackedSize", MAX_UNPACKED), ("fileCount", MAX_FILES)):
        if not positive_int(bundle.get(name), limit):
            raise ValueError("Invalid UI bundle size")
    if not isinstance(bundle.get("version"), str) or not 1 <= len(bundle["version"]) <= 128:
        raise ValueError("Invalid UI bundle version")
    for name in ("protocolMin", "protocolMax", "bridgeMin"):
        if not positive_int(bundle.get(name), 10000):
            raise ValueError("Invalid compatibility range")
    if bundle["protocolMin"] > bundle["protocolMax"]:
        raise ValueError("Invalid protocol interval")
    if not isinstance(bundle.get("minClientVersion"), str) or not VERSION.fullmatch(bundle["minClientVersion"]):
        raise ValueError("Invalid minimum client version")
    capabilities = bundle.get("capabilities")
    if not isinstance(capabilities, list) or len(capabilities) > 32 or any(
            not isinstance(c, str) or not re.fullmatch(r"[a-z_]{1,64}", c) for c in capabilities):
        raise ValueError("Invalid capabilities")
    installers = data.get("installers", {})
    if not isinstance(installers, dict) or set(installers) - PLATFORMS:
        raise ValueError("Invalid installers")
    for item in installers.values():
        if (not isinstance(item, dict) or not local_path(item.get("url"))
                or not isinstance(item.get("version"), str) or not VERSION.fullmatch(item["version"])
                or not isinstance(item.get("sha256"), str) or not HEX.fullmatch(item["sha256"])):
            raise ValueError("Invalid installer entry")
    return data


def verify_envelope(raw, public_pem):
    if len(raw) > MAX_METADATA:
        raise ValueError("UI metadata too large")
    envelope = json.loads(raw)
    if not isinstance(envelope, dict) or envelope.get("available") is not True or envelope.get("ok") is not True:
        raise ValueError("Invalid UI envelope")
    payload = base64.b64decode(envelope["payload"], validate=True)
    signature = base64.b64decode(envelope["signature"], validate=True)
    key = serialization.load_pem_public_key(public_pem)
    if not isinstance(key, rsa.RSAPublicKey) or key.key_size != 3072:
        raise ValueError("UI release key must be RSA-3072")
    key.verify(signature, payload, padding.PKCS1v15(), hashes.SHA256())
    return envelope, validate_payload(json.loads(payload))


def release_root():
    return Path(current_app.config.get("CLIENT_UI_RELEASE_DIR", ROOT / "client-ui-release"))


def read_release():
    path = release_root() / "update.json"
    if not path.exists():
        return None
    if path.is_symlink() or path.stat().st_size > MAX_METADATA:
        raise ValueError("Invalid UI release file")
    public = Path(current_app.config.get("CLIENT_UPDATE_PUBLIC_KEY", ROOT / "clients/update-public-key.pem"))
    return verify_envelope(path.read_bytes(), public.read_bytes())


@bp.get("/updates")
def updates():
    try:
        release = read_release()
        if release is None:
            return jsonify(ok=True, available=False)
        envelope, payload = release
        path = release_root() / "bundles" / (payload["bundle"]["id"] + ".zip")
        if path.is_symlink() or not path.is_file() or path.stat().st_size != payload["bundle"]["size"]:
            raise ValueError("UI bundle not ready")
        return jsonify(envelope)
    except Exception:
        current_app.logger.exception("Published client UI release is invalid")
        return jsonify(ok=False, error="客户端页面更新暂不可用", reason="invalid_ui_release"), 503


@bp.get("/ui/<bundle_id>.zip")
def bundle_download(bundle_id):
    if not HEX.fullmatch(bundle_id):
        return jsonify(ok=False, error="资源不存在"), 404
    try:
        release = read_release()
        if release is None or release[1]["bundle"]["id"] != bundle_id:
            return jsonify(ok=False, error="资源不存在"), 404
        bundle = release[1]["bundle"]
        path = release_root() / "bundles" / (bundle_id + ".zip")
        if path.is_symlink() or not path.is_file() or path.stat().st_size != bundle["size"]:
            raise ValueError("Missing or incomplete UI bundle")
        # Open before reading: a simultaneous atomic server deployment cannot swap
        # the checked file between validation and streaming its contents.
        stream = path.open("rb")
        try:
            digest = hashlib.sha256()
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
            if digest.hexdigest() != bundle["sha256"]:
                raise ValueError("Corrupt UI bundle")
            stream.seek(0)
            response = send_file(stream, mimetype="application/zip", download_name=bundle_id + ".zip",
                                 conditional=False, etag=False)
            response.call_on_close(stream.close)
            return response
        except Exception:
            stream.close()
            raise
    except Exception:
        current_app.logger.exception("Cannot serve published client UI bundle")
        return jsonify(ok=False, error="客户端页面更新暂不可用", reason="invalid_ui_release"), 503
