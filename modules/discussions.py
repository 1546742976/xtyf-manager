"""Read-only GitHub collaboration entry page."""
import os
import re
from typing import Final, TypedDict

from flask import Blueprint, current_app, render_template

from common import ROLE_NAMES, current_user, login_required


DEFAULT_GITHUB_REPOSITORY: Final = "SHM-white/xtyf-manager"
_OWNER_PATTERN: Final = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?"
_REPOSITORY_PATTERN: Final = r"[A-Za-z0-9_.-]+"
_REPOSITORY_VALUE_PATTERN: Final = re.compile(
    rf"(?P<owner>{_OWNER_PATTERN})/(?P<repository>{_REPOSITORY_PATTERN})"
)

discussions_bp = Blueprint("discussions", __name__)


class GitHubUrls(TypedDict):
    """Public GitHub URLs derived from a validated repository name."""

    repository: str
    discussions: str
    new_issue: str


def _github_urls(repository_value: str | None) -> GitHubUrls | None:
    """Derive public GitHub entry points from one validated owner/repository value."""
    if not isinstance(repository_value, str):
        return None
    matched = _REPOSITORY_VALUE_PATTERN.fullmatch(repository_value)
    if matched is None:
        return None
    owner = matched.group("owner")
    repository = matched.group("repository")
    base_url = f"https://github.com/{owner}/{repository}"
    return {
        "repository": base_url,
        "discussions": f"{base_url}/discussions",
        "new_issue": f"{base_url}/issues/new/choose",
    }


@discussions_bp.route("", strict_slashes=False)
@login_required
def index():
    """Render GitHub entry links without sending workspace data to GitHub."""
    configured_repository = current_app.config.get(
        "GITHUB_REPOSITORY",
        os.environ.get("GITHUB_REPOSITORY", DEFAULT_GITHUB_REPOSITORY),
    )
    return render_template(
        "discussions.html",
        me=current_user(),
        page="discussions",
        role_names=ROLE_NAMES,
        github_urls=_github_urls(configured_repository),
    )
