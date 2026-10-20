"""Guard existing GitHub Releases against overwriting published binaries.

A manually created empty stable Release is a valid upload target. Existing
assets are immutable from the automated publisher's perspective.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def validate_existing_release(data: dict, tag: str) -> None:
    """Allow only a non-draft, non-prerelease, mutable, *empty* stable Release."""
    if not isinstance(data, dict) or data.get("tagName") != tag:
        raise ValueError(f"Release metadata does not match requested tag {tag!r}")
    if data.get("isDraft") is not False or data.get("isPrerelease") is not False:
        raise ValueError("Existing Release must be a published, stable (non-draft) Release")
    if data.get("isImmutable") is not False:
        raise ValueError("Existing Release is immutable or immutability state is unknown")
    assets = data.get("assets")
    if not isinstance(assets, list):
        raise ValueError("Existing Release assets metadata is missing or malformed")
    if assets:
        names = [str(item.get("name", "<unknown>")) for item in assets if isinstance(item, dict)]
        raise ValueError(
            "Refusing to overwrite existing Release assets: "
            + ", ".join(names or ["<unknown>"])
            + ". Create a new version or investigate the previous upload."
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    args = parser.parse_args()
    validate_existing_release(json.loads(args.metadata.read_text(encoding="utf-8")), args.tag)
    print(f"Existing stable Release {args.tag} has no assets: safe to complete its upload.")


if __name__ == "__main__":
    main()
