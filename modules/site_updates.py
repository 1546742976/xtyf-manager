"""Authenticated server-release notice and read-only Git history endpoint."""
import json
from pathlib import Path
import re

from flask import Blueprint, jsonify, render_template

from common import BASE_DIR, ROLE_NAMES, current_user, login_required

bp = Blueprint("site_updates", __name__)
SNAPSHOT_PATH = Path(BASE_DIR) / "site-release.json"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def load_snapshot():
    """A missing/malformed old release never prevents the site from starting."""
    try:
        payload = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return None
    if not isinstance(payload, dict) or payload.get("format") != 1:
        return None
    commit = payload.get("commit")
    if not isinstance(commit, str) or not SHA_RE.fullmatch(commit):
        return None
    entries = payload.get("commits")
    if not isinstance(entries, list):
        return None
    commits = []
    for item in entries[:200]:
        if not isinstance(item, dict) or not isinstance(item.get("sha"), str) or not SHA_RE.fullmatch(item["sha"]):
            continue
        if not all(isinstance(item.get(key), str) for key in ("date", "author", "subject")):
            continue
        commits.append({
            "sha": item["sha"],
            "date": item["date"][:40],
            "author": item["author"][:120],
            "subject": item["subject"][:500],
        })
    commits.sort(key=lambda item: item["date"], reverse=True)
    built_at = payload.get("built_at", "")
    return {"commit": commit, "built_at": built_at[:40] if isinstance(built_at, str) else "",
            "commits": commits}


def no_cache(response):
    response.headers["Cache-Control"] = "private, no-store, max-age=0"
    response.headers["Vary"] = "Cookie"
    return response


@bp.route("/api/site-release")
@login_required
def version():
    snapshot = load_snapshot()
    return no_cache(jsonify(
        available=bool(snapshot),
        commit=snapshot["commit"] if snapshot else None,
        built_at=snapshot["built_at"] if snapshot else None,
        latest_subject=snapshot["commits"][0]["subject"] if snapshot and snapshot["commits"] else "",
    ))


@bp.route("/api/site-release/history")
@login_required
def history():
    """History stays on the server; native clients use the same authenticated API."""
    snapshot = load_snapshot()
    return no_cache(jsonify(available=bool(snapshot), **(snapshot or {})))


@bp.route("/updates")
@login_required
def timeline():
    return render_template("site_updates.html", me=current_user(), page="updates", role_names=ROLE_NAMES)
