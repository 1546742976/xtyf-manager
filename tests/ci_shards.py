"""Run one disjoint module group of the existing unittest discovery suite.

Keep all tests/test_*.py files in exactly one CI shard. New modules that do
not match a known feature fall back to ``platform`` rather than being skipped.
"""

from pathlib import Path
import sys
import unittest


TESTS_DIR = Path(__file__).resolve().parent
SHARDS = ("tasks", "members", "finance", "assets", "collaboration", "platform")
SPLIT_SHARDS = ("tasks-1of2", "tasks-2of2", "members-1of2", "members-2of2")


def shard_for(module_name: str) -> str:
    if module_name.startswith((
        "test_task", "test_workflows", "test_cross_project_schedule",
        "test_daily_projects", "test_completed_at", "test_alerts_and_topic_links",
        "test_repair_review",
    )):
        return "tasks"
    if module_name.startswith((
        "test_member", "test_account", "test_auth", "test_group",
        "test_vehicle_groups", "test_soft_deletes", "test_workspace_settings",
    )):
        return "members"
    if module_name.startswith(("test_invoice", "test_funds")):
        return "finance"
    if module_name.startswith("test_asset"):
        return "assets"
    if module_name.startswith((
        "test_collaboration", "test_discussions", "test_daily_mail",
        "test_migrations_collaboration", "test_progress_and_github_hub",
        "test_mail_installation",
    )):
        return "collaboration"
    return "platform"


def files_for_shard(shard: str, files: list[Path]) -> list[Path]:
    """Select disjoint file partitions that reconstruct the original group."""
    if shard in SHARDS:
        group, offset = shard, None
    elif shard in SPLIT_SHARDS:
        group, partition = shard.rsplit("-", 1)
        offset = int(partition[0]) - 1
    else:
        raise ValueError(f"Unknown CI shard: {shard}")
    selected = [path for path in sorted(files) if shard_for(path.stem) == group]
    return selected if offset is None else selected[offset::2]


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1] not in SHARDS + SPLIT_SHARDS:
        print(f"Usage: python tests/ci_shards.py <{'|'.join(SHARDS + SPLIT_SHARDS)}>", file=sys.stderr)
        return 2

    files = sorted(TESTS_DIR.glob("test_*.py"))
    if not files:
        print("No unittest modules found in tests/test_*.py", file=sys.stderr)
        return 2

    shard = argv[1]
    selected = files_for_shard(shard, files)
    if not selected:
        print(f"CI shard {shard!r} has no test modules", file=sys.stderr)
        return 2

    print(f"CI shard {shard}: {len(selected)} of {len(files)} modules", flush=True)
    for path in selected:
        print(f"  {path.name}", flush=True)

    # unittest discover -s tests imports each file by its module basename.
    # Load only this shard's modules to avoid executing other shards' tests.
    sys.path.insert(0, str(TESTS_DIR.parent))
    sys.path.insert(0, str(TESTS_DIR))
    loader = unittest.TestLoader()
    suite = unittest.TestSuite(loader.loadTestsFromName(path.stem) for path in selected)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
