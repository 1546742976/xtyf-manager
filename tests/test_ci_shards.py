"""Guard the CI module partition against accidental omissions."""

import unittest
from ci_shards import SHARDS, TESTS_DIR, files_for_shard, shard_for


class TestCIShards(unittest.TestCase):
    def test_every_discoverable_module_gets_one_known_shard(self):
        modules = list(TESTS_DIR.glob("test_*.py"))
        self.assertTrue(modules)
        self.assertTrue(all(shard_for(path.stem) in SHARDS for path in modules))

    def test_expected_feature_groups(self):
        self.assertEqual(shard_for("test_task_schedule"), "tasks")
        self.assertEqual(shard_for("test_member_profiles"), "members")
        self.assertEqual(shard_for("test_invoice_regression"), "finance")
        self.assertEqual(shard_for("test_assets"), "assets")
        self.assertEqual(shard_for("test_collaboration_backend"), "collaboration")

    def test_split_windows_groups_are_disjoint_and_complete(self):
        files = list(TESTS_DIR.glob("test_*.py"))
        for group in ("tasks", "members"):
            with self.subTest(group=group):
                full = set(files_for_shard(group, files))
                first = set(files_for_shard(f"{group}-1of2", files))
                second = set(files_for_shard(f"{group}-2of2", files))
                self.assertTrue(first)
                self.assertTrue(second)
                self.assertFalse(first & second)
                self.assertEqual(first | second, full)

    def test_new_unknown_modules_fall_back_to_platform(self):
        self.assertEqual(shard_for("test_new_backend_feature"), "platform")
