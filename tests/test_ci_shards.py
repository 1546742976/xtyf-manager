"""Guard the CI module partition against accidental omissions."""

import unittest
from ci_shards import SHARDS, TESTS_DIR, shard_for


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

    def test_new_unknown_modules_fall_back_to_platform(self):
        self.assertEqual(shard_for("test_new_backend_feature"), "platform")
