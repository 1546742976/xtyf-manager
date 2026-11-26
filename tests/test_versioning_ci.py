"""Deterministic build version and Android upgrade order."""
import unittest
from clients.versioning_ci import version_code,parse_refs
class VersioningTests(unittest.TestCase):
    def test_android_order(self):
        self.assertEqual(version_code("1.2.0-beta.1"),102000001)
        self.assertEqual(version_code("1.2.0-beta.2"),102000002)
        self.assertEqual(version_code("1.2.0"),102009999)
        self.assertLess(version_code("1.2.0-beta.998"),version_code("1.2.0"))
        self.assertLess(version_code("1.2.0"),version_code("1.2.1-beta.1"))
        self.assertGreater(version_code("1.2.0-beta.1"),14)
    def test_invalid(self):
        for s in ("1.2.0-beta.0","1.2.0-beta.9999","1.2.0-rc.1","01.2.0","20.0.0","1.100.0","1.2.100"):
            with self.subTest(s=s),self.assertRaises(ValueError):version_code(s)
    def test_beta_refs(self):
        refs="a\trefs/tags/v1.2.0-beta.1\nb\trefs/tags/v1.2.0-beta.23\nc\trefs/tags/v1.2.0-beta.2^{}\nd\trefs/tags/v1.3.0-beta.100\n"
        self.assertEqual(parse_refs(refs,"1.2.0"),23)
        self.assertEqual(parse_refs(refs,"1.4.0"),0)


class SelectionTests(unittest.TestCase):
    SHA = "a" * 40

    def check(self, *, intent=False, release_pr=False, existing=None, ref=""):
        import json
        import os
        from pathlib import Path
        from subprocess import CompletedProcess
        from tempfile import TemporaryDirectory
        from unittest.mock import patch
        from clients import versioning_ci as ci

        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/"clients").mkdir()
            (root/".github").mkdir()
            (root/"clients/config.json").write_text(json.dumps({
                "version": "1.2.0" if intent else "1.1.0",
                "versionCode": 102009999 if intent else 14,
                "name": "XTYF", "serverUrl":"http://localhost",
            }))
            (root/"clients/release-plan.json").write_text(json.dumps({
                "nextVersion":"1.2.1" if intent else "1.2.0"
            }))
            if intent:
                (root/".github/release-intent.json").write_text(json.dumps({"version":"1.2.0"}))
            def command(args, **kwargs):
                if args[:2]==["gh","api"]:
                    prs=[{"merged_at":"2026-10-25","base":{"ref":"main"},
                          "head":{"ref":"release/1.2.0"}}] if release_pr else []
                    return json.dumps(prs)
                return ref
            def release(args, **kwargs):
                if existing is None:return CompletedProcess(args,1,stdout="",stderr="release not found")
                if isinstance(existing,int):return CompletedProcess(args,0,stdout=str(existing)+"\n",stderr="")
                return CompletedProcess(args,0,stdout=json.dumps(existing),stderr="")
            output=root/"metadata.txt"
            with patch.object(ci,"ROOT",root),patch("sys.argv",["cli","select","--output",str(output)]),\
                 patch.dict(os.environ,{"GITHUB_SHA":self.SHA,"GITHUB_REPOSITORY":"SHM-white/xtyf-manager"}),\
                 patch.object(ci.subprocess,"check_output",side_effect=command),\
                 patch.object(ci.subprocess,"run",side_effect=release):
                ci.main()
            return dict(line.strip().split("=",1) for line in output.read_text().splitlines())

    def test_initial_beta(self):
        result=self.check()
        self.assertEqual(result["tag"],"v1.2.0-beta.1")
        self.assertEqual(result["already_published"],"false")

    def test_stable_merge_only(self):
        self.assertEqual(self.check(intent=True,release_pr=True)["tag"],"v1.2.0")
        self.assertEqual(self.check(intent=True,release_pr=True,existing=9)["already_published"],"true")
        self.assertEqual(self.check(intent=True,release_pr=False)["tag"],"v1.2.1-beta.1")

    def test_rerun_existing_beta(self):
        tag="v1.2.0-beta.3"
        refs=self.SHA+"\trefs/tags/"+tag+"\n"
        result=self.check(ref=refs,existing={"draft":False,"prerelease":True,"count":9})
        self.assertEqual(result["tag"],tag)
        self.assertEqual(result["already_published"],"true")
