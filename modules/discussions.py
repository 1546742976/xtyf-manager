"""Authenticated, same-origin GitHub Issues and Discussions collaboration gateway."""
from __future__ import annotations

import json
import os
import re
from typing import Final, TypedDict
from urllib.error import HTTPError, URLError
from urllib.parse import quote
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


class GitHubError(Exception):
    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


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
    }


def _token() -> str:
    return str(current_app.config.get("GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN") or "").strip()


def _write_enabled() -> bool:
    enabled = current_app.config.get("GITHUB_WRITE_ENABLED", os.environ.get("GITHUB_WRITE_ENABLED", "0"))
    return bool(_token()) and str(enabled).lower() in ("1", "true", "yes")


def _remote(path: str, payload: dict | None = None, graphql: bool = False):
    """Fixed GitHub API origin and validated repository prevent user-controlled SSRF."""
    repo = _repository()
    if repo is None:
        raise GitHubError("尚未配置有效的 GitHub 仓库", 503)
    token = _token()
    if graphql and not token:
        raise GitHubError("查看 Discussions 需要服务器配置 GITHUB_TOKEN", 503)
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
        if error.code in (401, 403):
            raise GitHubError("GitHub 令牌无权限或 API 速率受限", 503) from error
        if error.code == 404:
            raise GitHubError("GitHub 资源不存在或无访问权限", 404) from error
        raise GitHubError(f"GitHub API 暂不可用（HTTP {error.code}）") from error
    except (URLError, TimeoutError, ValueError) as error:
        raise GitHubError("无法连接 GitHub，请稍后重试") from error
    if graphql:
        if result.get("errors"):
            raise GitHubError("GitHub GraphQL 请求失败，请检查仓库权限或输入内容")
        return result.get("data") or {}
    return result


def _graphql(query: str, variables: dict | None = None):
    return _remote("", {"query": query, "variables": variables or {}}, graphql=True)


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
        github_can_write=_write_enabled(), github_has_token=bool(_token()),
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
                           categories=[], can_write=_write_enabled())
        data = (_graphql(_DISCUSSION_LIST, _parts()).get("repository") or {})
        return jsonify(
            ok=True, items=[_discussion(item) for item in (data.get("discussions") or {}).get("nodes") or []],
            categories=(data.get("discussionCategories") or {}).get("nodes") or [],
            can_write=_write_enabled(),
        )
    except GitHubError as error:
        return jsonify(ok=False, error=str(error)), error.status


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
        item = ((_graphql(_DISCUSSION_DETAIL, _parts() | {"number": number})
                .get("repository") or {}).get("discussion"))
        if item is None:
            return jsonify(ok=False, error="讨论不存在"), 404
        return jsonify(ok=True, item=_discussion(item),
                       comments=_comments((item.get("commentsPage") or {}).get("nodes") or []))
    except GitHubError as error:
        return jsonify(ok=False, error=str(error)), error.status


def _text(data: dict, key: str, limit: int, required: bool = True) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        raise ValueError(f"{key} 格式不合法")
    result = value.strip()
    if len(result) > limit or (required and not result):
        raise ValueError(f"{key} 不能为空或超过 {limit} 字")
    return result


def _attributed(body: str) -> str:
    actor = current_user()
    name = str(actor["name"]).replace("\n", " ").replace("\r", " ")[:80]
    return body + f"\n\n---\n由巡天御风管理系统成员「{name}」（ID #{actor['id']}）通过工作台提交。"


@discussions_bp.post("/api/post")
@login_required
def post():
    if not _write_enabled():
        return jsonify(ok=False, error="站内提交未启用：请配置 GITHUB_TOKEN 和 GITHUB_WRITE_ENABLED=1"), 403
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(ok=False, error="请求格式不正确"), 400
    kind = data.get("kind")
    if kind not in ("issues", "discussions"):
        return jsonify(ok=False, error="板块不合法"), 400
    action = data.get("action")
    if action not in ("create", "comment"):
        return jsonify(ok=False, error="操作不合法"), 400
    try:
        body = _attributed(_text(data, "body", 8000))
        if action == "create":
            title = _text(data, "title", 180)
            if kind == "issues":
                created = _remote("/issues", {"title": title, "body": body})
                number = created["number"]
            else:
                config = (_graphql(_DISCUSSION_LIST, _parts()).get("repository") or {})
                categories = (config.get("discussionCategories") or {}).get("nodes") or []
                category = str(data.get("category_id") or "")
                if category not in {item["id"] for item in categories}:
                    return jsonify(ok=False, error="请选择有效的讨论分类"), 400
                repo_info = _graphql(
                    "query($owner:String!,$name:String!){repository(owner:$owner,name:$name){id}}",
                    _parts(),
                )
                created = _graphql(
                    "mutation($repo:ID!,$category:ID!,$title:String!,$body:String!){"
                    "createDiscussion(input:{repositoryId:$repo,categoryId:$category,title:$title,body:$body})"
                    "{discussion{number}}}",
                    {"repo": repo_info["repository"]["id"], "category": category, "title": title, "body": body},
                )
                number = created["createDiscussion"]["discussion"]["number"]
        else:
            number = data.get("number")
            if isinstance(number, bool) or not isinstance(number, int) or not 0 < number < 2**31:
                return jsonify(ok=False, error="主题编号不合法"), 400
            if kind == "issues":
                _remote(f"/issues/{number}/comments", {"body": body})
            else:
                item = ((_graphql(_DISCUSSION_DETAIL, _parts() | {"number": number})
                        .get("repository") or {}).get("discussion"))
                if item is None:
                    return jsonify(ok=False, error="讨论不存在"), 404
                _graphql(
                    "mutation($id:ID!,$body:String!){addDiscussionComment("
                    "input:{discussionId:$id,body:$body}){comment{id}}}",
                    {"id": item["id"], "body": body},
                )
        return jsonify(ok=True, number=number)
    except ValueError as error:
        return jsonify(ok=False, error=str(error)), 400
    except (GitHubError, KeyError, TypeError) as error:
        if isinstance(error, GitHubError):
            return jsonify(ok=False, error=str(error)), error.status
        return jsonify(ok=False, error="GitHub 返回了不符合预期的数据"), 502
