"""Read public LCSC product metadata without sending packaging or order details."""

import copy
import json
import re
import socket
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

MAX_PAGE_BYTES = 2 * 1024 * 1024
LOOKUP_TIMEOUT = 8
CACHE_LIMIT = 128
_cache = OrderedDict()
_cache_lock = threading.Lock()


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # The only network destination is the fixed public product-page host.
        return None


class _ProductScripts(HTMLParser):
    def __init__(self):
        super().__init__()
        self.scripts = []
        self.parts = None

    def handle_starttag(self, tag, attrs):
        if tag == "script" and dict(attrs).get("type", "").lower() == "application/ld+json":
            self.parts = []

    def handle_data(self, value):
        if self.parts is not None:
            self.parts.append(value)

    def handle_endtag(self, tag):
        if tag == "script" and self.parts is not None:
            self.scripts.append("".join(self.parts))
            self.parts = None


def _string(value, maximum=200):
    return re.sub(r"\s+", " ", value).strip()[:maximum] if isinstance(value, str) else ""


def _product_metadata(page, code, url):
    parser = _ProductScripts()
    parser.feed(page)
    for script in parser.scripts:
        try:
            document = json.loads(script)
        except (ValueError, RecursionError):
            continue
        nodes = document if isinstance(document, list) else [document]
        # JSON-LD can put the product in the top-level @graph.
        nodes = nodes + [child for node in nodes
                         if isinstance(node, dict) and isinstance(node.get("@graph"), list)
                         for child in node["@graph"]]
        for node in nodes:
            if not isinstance(node, dict):
                continue
            kinds = node.get("@type", [])
            if isinstance(kinds, str):
                kinds = [kinds]
            elif not isinstance(kinds, list):
                kinds = []
            if "Product" not in kinds or _string(node.get("sku")).upper() != code:
                continue
            name, model = _string(node.get("name")), _string(node.get("mpn"))
            if not name or not model:
                continue
            brand = node.get("brand", {})
            manufacturer = _string(brand.get("name") if isinstance(brand, dict) else brand)
            properties = node.get("additionalProperty", [])
            package = next((_string(p.get("value")) for p in properties
                            if isinstance(p, dict) and _string(p.get("name")).lower() == "package"), "") \
                if isinstance(properties, list) else ""
            return {"code": code, "name": name, "model": model, "manufacturer": manufacturer,
                    "package": package, "url": url, "source": "LCSC 官方商品页"}
    return None


def lookup_lcsc(code):
    """Fetch by a validated C number; lookup never creates local identifiers/stock."""
    if not isinstance(code, str) or not re.fullmatch(r"C[1-9][0-9]{0,17}", code):
        raise ValueError("Invalid LCSC product code")
    now = time.monotonic()
    with _cache_lock:
        cached = _cache.get(code)
        if cached and cached[0] > now:
            _cache.move_to_end(code)
            return {**copy.deepcopy(cached[1]), "cached": True}
    url = f"https://www.lcsc.com/product-detail/{code}.html"
    result = {"configured": True, "status": "unavailable", "product": None,
              "url": url, "cached": False, "fetched_at": datetime.now(timezone.utc).isoformat()}
    try:
        request = Request(url, headers={"User-Agent": "xtyf-manager/1.0 (public product lookup)",
                                       "Accept": "text/html", "Accept-Encoding": "identity"})
        with build_opener(_NoRedirect()).open(request, timeout=LOOKUP_TIMEOUT) as response:
            size = response.headers.get("Content-Length", "")
            if size.isdigit() and int(size) > MAX_PAGE_BYTES:
                raise ValueError("商品页面过大，已停止读取")
            page = response.read(MAX_PAGE_BYTES + 1)
            if len(page) > MAX_PAGE_BYTES:
                raise ValueError("商品页面过大，已停止读取")
            product = _product_metadata(page.decode("utf-8", errors="replace"), code, url)
        if product:
            result.update(status="found", product=product, message="已从 LCSC 官方商品页获取资料")
        else:
            result["message"] = "商城页面未提供可核对的商品资料，请打开官方商品页确认"
    except HTTPError as error:
        if error.code in (404, 410):
            result.update(status="not_found", message="商城未找到此商品编号，请核对标签或打开官方商品页")
        elif error.code in (403, 429):
            result["message"] = "商城暂时限制自动查询，请稍后重试或打开官方商品页"
        elif 300 <= error.code < 400:
            result["message"] = "商城商品页发生跳转，暂无法自动获取，请打开官方商品页"
        else:
            result["message"] = "商城暂时无法响应，请稍后重试或打开官方商品页"
    except (TimeoutError, socket.timeout):
        result["message"] = "商城查询超时；标签解析和本地匹配仍可使用"
    except (URLError, OSError):
        result["message"] = "暂时无法连接商城；标签解析和本地匹配仍可使用"
    except (ValueError, RecursionError):
        result["message"] = "商城资料格式暂不支持自动读取，请打开官方商品页"
    ttl = 300 if result["status"] == "found" else 30
    with _cache_lock:
        _cache[code] = (time.monotonic() + ttl, copy.deepcopy(result))
        _cache.move_to_end(code)
        while len(_cache) > CACHE_LIMIT:
            _cache.popitem(last=False)
    return result
