"""Read-only GitHub Issues/Discussions mirror; participation stays on GitHub."""
from __future__ import annotations

import json
import os
import re
import hashlib
import time
from html.parser import HTMLParser
from xml.etree import ElementTree as ET
from typing import Final, TypedDict
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from flask import Blueprint, current_app, jsonify, render_template, request

from common import ROLE_NAMES, current_user, login_required

DEFAULT_GITHUB_REPOSITORY: Final = "SHM-white/xtyf-manager"
_OWNER_PATTERN: Final = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?"
_REPOSITORY_PATTERN: Final = r"[A-Za-z0-9_.-]+"
_REPOSITORY_VALUE_PATTERN: Final = re.compile(
    rf"(?P<owner>{_OWNER_PATTERN})/(?P<repository>{_REPOSITORY_PATTERN})"
)
discussions_bp = Blueprint("discussions", __name__)


class GitHubUrls(TypedDict):
    repository: str
    discussions: str
    new_issue: str
    new_discussion: str


class GitHubError(Exception):
    def __init__(self, message: str, status: int = 502, retry_after: int = 0):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


def _repository() -> str | None:
    name = current_app.config.get(
        "GITHUB_REPOSITORY",
        os.environ.get("GITHUB_REPOSITORY", DEFAULT_GITHUB_REPOSITORY),
    )
    return name if isinstance(name, str) and _REPOSITORY_VALUE_PATTERN.fullmatch(name) else None


def _github_urls(repository_value: str | None) -> GitHubUrls | None:
    if not isinstance(repository_value, str):
        return None
    matched = _REPOSITORY_VALUE_PATTERN.fullmatch(repository_value)
    if matched is None:
        return None
    base_url = f"https://github.com/{matched.group('owner')}/{matched.group('repository')}"
    return {
        "repository": base_url,
        "discussions": f"{base_url}/discussions",
        "new_issue": f"{base_url}/issues/new/choose",
        "new_discussion": f"{base_url}/discussions/new/choose",
    }


def _token() -> str:
    return str(current_app.config.get("GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN") or "").strip()


def _remote(path: str, payload: dict | None = None, graphql: bool = False):
    """Share short-lived reads within a worker and respect upstream cooldowns."""
    scope = (_repository(), hashlib.sha256(_token().encode()).hexdigest())
    key = scope + (path, graphql, json.dumps(payload, sort_keys=True))
    cache = current_app.extensions.setdefault('github_read_cache', {})
    cooldowns = current_app.extensions.setdefault('github_cooldowns', {})
    now = time.time()
    if key in cache and cache[key][0] > now:
        return cache[key][1]
    cooldown = cooldowns.get(scope)
    if cooldown and cooldown[0] > now:
        raise GitHubError(cooldown[1], 503, max(1, int(cooldown[0] - now)))
    try:
        result = _remote_uncached(path, payload, graphql)
    except GitHubError as error:
        if error.retry_after:
            if len(cooldowns) >= 64:
                cooldowns.clear()
            cooldowns[scope] = (now + error.retry_after, str(error))
        raise
    if len(cache) >= 128:
        cache.clear()
    cache[key] = (time.time() + 60, result)
    return result


def _remote_uncached(path: str, payload: dict | None = None, graphql: bool = False):
    """Fixed GitHub API origin and validated repository prevent user-controlled SSRF."""
    repo = _repository()
    if repo is None:
        raise GitHubError("尚未配置有效的 GitHub 仓库", 503)
    token = _token()
    if graphql and not token:
        raise GitHubError("完整读取 Discussions 需要配置只读 GITHUB_TOKEN", 503)
    if payload is not None and not graphql:
        raise GitHubError("工作台仅允许读取，发布请前往 GitHub", 405)
    url = "https://api.github.com/graphql" if graphql else f"https://api.github.com/repos/{repo}{path}"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "xtyf-manager-collaboration",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = Request(url, headers=headers, data=body, method="POST" if body is not None else "GET")
    try:
        with urlopen(req, timeout=8) as response:
            content = response.read(1_000_001)
            if len(content) > 1_000_000:
                raise GitHubError("GitHub 响应超过限制")
            result = json.loads(content)
    except HTTPError as error:
        headers = error.headers or {}
        if error.code in (403, 429) and (headers.get('X-RateLimit-Remaining') == '0' or error.code == 429 or headers.get('Retry-After')):
            try:
                delay = int(headers.get('Retry-After') or max(60, float(headers.get('X-RateLimit-Reset', 0)) - time.time()))
            except (ValueError, TypeError):
                delay = 60
            delay = min(3600, max(1, delay))
            raise GitHubError("GitHub 读取额度暂时用尽，额度恢复后可重新加载；现在可以前往 GitHub 查看。", 503, delay) from error
        if error.code in (401, 403):
            raise GitHubError("GitHub 读取授权不可用，请联系管理员检查连接设置；也可前往 GitHub 查看。", 503) from error
        if error.code == 404:
            raise GitHubError("GitHub 资源不存在或无访问权限", 404) from error
        raise GitHubError(f"GitHub API 暂不可用（HTTP {error.code}）") from error
    except (URLError, TimeoutError, ValueError, OSError) as error:
        raise GitHubError("无法连接 GitHub，请稍后重试") from error
    if graphql:
        if result.get("errors"):
            raise GitHubError("GitHub GraphQL 请求失败，请检查仓库权限或输入内容")
        return result.get("data") or {}
    return result


def _graphql(query: str, variables: dict | None = None):
    return _remote("", {"query": query, "variables": variables or {}}, graphql=True)


# Public GitHub Discussions expose a native Atom feed. It needs no token,
# although unlike GraphQL it does not expose discussion comments or counts.
_ATOM_NS = "{http://www.w3.org/2005/Atom}"
_ATOM_MAX_BYTES = 1_000_000
_DISCUSSION_LINK = re.compile(r"^https://github\.com/([^/]+)/([^/]+)/discussions/([1-9][0-9]*)/?$")


class _PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str):
        self.parts.append(data)

    def handle_starttag(self, tag: str, attrs):
        if tag in ("p", "br", "div", "li", "h1", "h2", "h3"):
            self.parts.append("\n")


def _atom_items(feed: bytes, repo: str) -> list[dict]:
    try:
        root = ET.fromstring(feed)
    except ET.ParseError as error:
        raise GitHubError("GitHub Discussions 订阅源格式不正确") from error
    if root.tag != _ATOM_NS + "feed":
        raise GitHubError("GitHub Discussions 订阅源格式不正确")
    results = []
    for entry in root.findall(_ATOM_NS + "entry")[:30]:
        link = next((link.get("href", "") for link in entry.findall(_ATOM_NS + "link")
                     if link.get("rel", "alternate") == "alternate"), "")
        match = _DISCUSSION_LINK.fullmatch(link)
        if match is None or match.group(1) + "/" + match.group(2) != repo:
            continue
        raw_body = entry.findtext(_ATOM_NS + "content") or entry.findtext(_ATOM_NS + "summary") or ""
        body_type = entry.find(_ATOM_NS + "content")
        if body_type is None:
            body_type = entry.find(_ATOM_NS + "summary")
        if body_type is not None and body_type.get("type") == "html":
            parser = _PlainText()
            parser.feed(raw_body[:200_000])
            raw_body = "".join(parser.parts)
        results.append({
            "number": int(match.group(3)),
            "title": (entry.findtext(_ATOM_NS + "title") or "").strip()[:300],
            "body": raw_body.strip()[:20_000],
            "author": (entry.findtext(_ATOM_NS + "author/" + _ATOM_NS + "name") or "未知"),
            "updated_at": entry.findtext(_ATOM_NS + "updated") or "",
            "category": (entry.find(_ATOM_NS + "category").get("term", "")
                         if entry.find(_ATOM_NS + "category") is not None else ""),
            "comments_count": None,
            "url": link,
        })
    return results


def _atom_feed() -> list[dict]:
    repo = _repository()
    if repo is None:
        raise GitHubError("尚未配置有效的 GitHub 仓库", 503)
    # Repository value is strictly validated; the request host is fixed.
    url = f"https://github.com/{repo}/discussions.atom"
    req = Request(url, headers={
        "User-Agent": "xtyf-manager-collaboration",
        "Accept": "application/atom+xml",
    })
    try:
        with urlopen(req, timeout=8) as response:
            feed = response.read(_ATOM_MAX_BYTES + 1)
        if len(feed) > _ATOM_MAX_BYTES:
            raise GitHubError("GitHub Discussions 订阅源超过大小限制")
        return _atom_items(feed, repo)
    except HTTPError as error:
        if error.code in (404, 406):
            raise GitHubError("该仓库的公开 Discussions 订阅源不可用", 503) from error
        raise GitHubError(f"GitHub Discussions 读取失败（HTTP {error.code}）") from error
    except (URLError, TimeoutError, OSError) as error:
        raise GitHubError("暂时无法连接 GitHub Discussions", 503) from error


def _issue(item: dict) -> dict:
    return {
        "number": item.get("number"), "title": item.get("title") or "",
        "body": item.get("body") or "", "author": (item.get("user") or {}).get("login") or "未知",
        "updated_at": item.get("updated_at") or "", "comments_count": item.get("comments") or 0,
        "url": item.get("html_url") or "",
    }


def _discussion(item: dict) -> dict:
    return {
        "number": item.get("number"), "title": item.get("title") or "",
        "body": item.get("bodyText") or "", "author": (item.get("author") or {}).get("login") or "未知",
        "updated_at": item.get("updatedAt") or "", "category": (item.get("category") or {}).get("name") or "",
        "comments_count": ((item.get("comments") or {}).get("totalCount") or 0),
        "url": item.get("url") or "",
    }


_DISCUSSION_LIST = """
query($owner:String!, $name:String!) {
  repository(owner:$owner,name:$name) {
    discussions(first:30,orderBy:{field:UPDATED_AT,direction:DESC}) {
      nodes { id number title bodyText updatedAt url author { login }
              category { name } comments(first:1) { totalCount } }
    }
    discussionCategories(first:50) { nodes { id name } }
  }
}
"""
_DISCUSSION_DETAIL = """
query($owner:String!, $name:String!, $number:Int!) {
  repository(owner:$owner,name:$name) {
    discussion(number:$number) {
      id number title bodyText updatedAt url author { login } category { name }
      comments(first:1) { totalCount }
      commentsPage:comments(first:50) {
        nodes { id bodyText createdAt author { login }
          replies(first:20) { nodes { id bodyText createdAt author { login } } }
        }
      }
    }
  }
}
"""


def _parts() -> dict:
    owner, name = _repository().split("/", 1)
    return {"owner": owner, "name": name}


def _comments(nodes: list[dict]) -> list[dict]:
    comments = []
    for item in nodes:
        comments.append({
            "author": (item.get("author") or {}).get("login") or "未知",
            "body": item.get("bodyText") or "",
            "created_at": item.get("createdAt") or "",
        })
        for child in (item.get("replies") or {}).get("nodes") or []:
            comments.append({
                "author": (child.get("author") or {}).get("login") or "未知",
                "body": "↳ " + (child.get("bodyText") or ""),
                "created_at": child.get("createdAt") or "",
            })
    return comments


@discussions_bp.route("", strict_slashes=False)
@login_required
def index():
    repo = _repository()
    return render_template(
        "discussions.html", me=current_user(), page="discussions",
        role_names=ROLE_NAMES, github_urls=_github_urls(repo),
    )


@discussions_bp.get("/api/items")
@login_required
def items():
    kind = request.args.get("kind", "issues")
    if kind not in ("issues", "discussions"):
        return jsonify(ok=False, error="板块不合法"), 400
    try:
        if kind == "issues":
            raw_page = request.args.get("page", "1")
            if not raw_page.isascii() or not raw_page.isdecimal() or not 1 <= int(raw_page) <= 20:
                return jsonify(ok=False, error="页码不合法"), 400
            items = _remote(f"/issues?state=all&per_page=30&page={int(raw_page)}")
            return jsonify(ok=True, items=[_issue(item) for item in items if "pull_request" not in item],
                           source="rest", comments_available=True, has_next=len(items) == 30 and int(raw_page) < 20)
        if _token():
            try:
                data = (_graphql(_DISCUSSION_LIST, _parts()).get("repository") or {})
                return jsonify(
                    ok=True, items=[_discussion(item) for item in
                                    (data.get("discussions") or {}).get("nodes") or []],
                    source="graphql", comments_available=True,
                )
            except GitHubError:
                # A missing/expired read token must not hide public discussions.
                pass
        return jsonify(ok=True, items=_atom_feed(), source="atom", comments_available=False)
    except GitHubError as error:
        return jsonify(ok=False, error=str(error), retry_after=error.retry_after), error.status


@discussions_bp.get("/api/item/<kind>/<int:number>")
@login_required
def detail(kind: str, number: int):
    if kind not in ("issues", "discussions") or not 0 < number < 2**31:
        return jsonify(ok=False, error="资源 ID 不合法"), 400
    try:
        if kind == "issues":
            item = _remote(f"/issues/{number}")
            if "pull_request" in item:
                return jsonify(ok=False, error="此页面不包含 Pull Requests"), 400
            raw_comments = _remote(f"/issues/{number}/comments?per_page=100")
            comments = [{
                "author": (c.get("user") or {}).get("login") or "未知",
                "body": c.get("body") or "", "created_at": c.get("created_at") or "",
            } for c in raw_comments]
            return jsonify(ok=True, item=_issue(item), comments=comments)
        if _token():
            try:
                item = ((_graphql(_DISCUSSION_DETAIL, _parts() | {"number": number})
                        .get("repository") or {}).get("discussion"))
                if item is None:
                    return jsonify(ok=False, error="讨论不存在"), 404
                return jsonify(ok=True, item=_discussion(item),
                               comments=_comments((item.get("commentsPage") or {}).get("nodes") or []),
                               comments_available=True)
            except GitHubError:
                pass
        item = next((entry for entry in _atom_feed() if entry["number"] == number), None)
        if item is None:
            return jsonify(ok=False, error="当前订阅源未收录该讨论，请在 GitHub 查看", url=_github_urls(_repository())["discussions"]), 404
        return jsonify(ok=True, item=item, comments=[], comments_available=False)
    except GitHubError as error:
        return jsonify(ok=False, error=str(error), retry_after=error.retry_after), error.status
