"""Write a bounded, immutable Git history snapshot into a server release.

This script runs on the packaging/development machine, never on the web server.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess

MAX_COMMITS = 200
SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def create_snapshot(repo_root, commit, max_commits=MAX_COMMITS):
    if not SHA_RE.fullmatch(commit):
        raise ValueError("Invalid release commit")
    if not 1 <= max_commits <= MAX_COMMITS:
        raise ValueError("Invalid history limit")
    output = subprocess.check_output(
        ["git", "log", "--max-count=" + str(max_commits),
         "--format=%H%x1f%cI%x1f%an%x1f%s%x1e", commit],
        cwd=repo_root,
    ).decode("utf-8", errors="replace")
    commits = []
    for record in output.split("\x1e"):
        columns = record.strip("\r\n").split("\x1f")
        if len(columns) != 4:
            continue
        sha, date, author, subject = columns
        if not SHA_RE.fullmatch(sha):
            continue
        commits.append({
            "sha": sha,
            "date": date[:40],
            "author": author[:120],
            "subject": subject[:500],
        })
    if not commits or commits[0]["sha"] != commit:
        raise RuntimeError("HEAD history does not match packaged source commit")
    snapshot = {
        "format": 1,
        "commit": commit,
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "commits": commits,
    }
    return (json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=args.repo, text=True).strip()
    args.output.write_bytes(create_snapshot(args.repo, commit))


if __name__ == "__main__":
    main()
