# -*- coding: utf-8 -*-
import contextlib
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest import mock
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("release_tool_test", ROOT / "release_tool.py")
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)
REPO = "ByteRax/zcode-token-usage-statusbar"

# An executable gh stub; all GitHub calls below are isolated and have no network access.
GH_STUB = r'''#!/usr/bin/env python3
import json, os, pathlib, sys
root = pathlib.Path(os.environ["STUB_ROOT"])
args = sys.argv[1:]
with (root / "gh.log").open("a") as log:
    log.write(json.dumps(args) + "\n")
mode = os.environ.get("STUB_MODE", "ok")
state_path = root / "gh-state.json"
state = json.loads(state_path.read_text()) if state_path.exists() else {}
def emit(value):
    print(json.dumps(value))
def release(draft):
    return {"draft": draft, "prerelease": False, "tag_name": "v1.2.3", "assets": state.get("assets", [])}
if args[:2] == ["auth", "status"]:
    sys.exit(1 if mode == "noauth" else 0)
if args[0] == "api":
    endpoint = args[1]
    if "/releases/assets/" in endpoint:
        asset_id = int(endpoint.rsplit("/", 1)[1])
        data = pathlib.Path(state["paths"][str(asset_id)]).read_bytes()
        sys.stdout.buffer.write(b"tampered" if mode == "badhash" else data)
    elif endpoint.endswith("/releases/tags/v1.2.3"):
        emit(release(state.get("draft", True)))
    elif "/git/ref/tags/" in endpoint:
        head = (root / "head").read_text().strip()
        emit({"object": {"type": "commit", "sha": "0" * 40 if mode == "badtag" else head}})
    elif endpoint.endswith("/commits/v1.2.3"):
        emit({"sha": (root / "head").read_text().strip()})
    elif "/releases?" in endpoint:
        emit([[release(state["draft"])]] if "draft" in state else
             [[{"tag_name": "v1.2.3", "draft": True}]] if mode == "exists" else [[]])
    else:
        emit({"full_name": "ByteRax/zcode-token-usage-statusbar", "permissions": {"push": mode != "readonly"}})
elif args[:2] == ["release", "create"]:
    state["draft"] = True
elif args[:2] == ["release", "upload"]:
    paths = args[3:5]
    state["paths"] = {str(i + 1): p for i, p in enumerate(paths)}
    state["assets"] = [{"id": i + 1, "name": pathlib.Path(p).name,
                        "size": pathlib.Path(p).stat().st_size, "state": "uploaded"}
                       for i, p in enumerate(paths)]
elif args[:2] == ["release", "edit"]:
    state["draft"] = mode == "badpublish"
else:
    sys.exit("unexpected gh command: " + repr(args))
state_path.write_text(json.dumps(state))
'''


class ReleaseToolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base / "repo"
        self.root.mkdir()
        for name in tool.NAMES:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            source = ROOT / name
            path.write_bytes(source.read_bytes() if source.is_file() else b"// fixture source\n")
        helper = (ROOT / "release_unpack.py").read_bytes()
        (self.root / tool.PREFIX / "release_unpack.py").write_bytes(helper)
        manifest = {"name": tool.PLUGIN, "version": "1.2.3", "commands": "commands", "skills": "skills"}
        for name in (".zcode-plugin/plugin.json", ".claude-plugin/plugin.json"):
            (self.root / tool.PREFIX / name).write_text(json.dumps(manifest))
        market = {"name": "zusage-marketplace", "plugins": [{"name": tool.PLUGIN, "version": "1.2.3",
                  "source": "./plugins/" + tool.PLUGIN}]}
        (self.root / "marketplace.json").write_text(json.dumps(market))
        for name in tool.SYNC_FILES:
            (self.root / name).write_bytes((self.root / tool.PREFIX / name).read_bytes())
        script = (ROOT / "release_tool.py").read_bytes()
        (self.root / "release_tool.py").write_bytes(script)
        (self.root / "sync_plugin.py").write_text("# fixture\n")
        self.git("init", "--quiet")
        self.git("config", "user.name", "Release Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("add", ".")
        self.git("commit", "--quiet", "-m", "fixture")
        self.git("tag", "v1.2.3")
        self.git("remote", "add", "origin", "https://github.com/xhwxt/zcode-token-usage-statusbar.git")
        self.git("remote", "add", "writable", "git@github.com:" + REPO + ".git")
        (self.base / "head").write_text(self.git("rev-parse", "HEAD").strip())
        bin_dir = self.base / "bin"
        bin_dir.mkdir()
        gh = bin_dir / "gh"
        gh.write_text(GH_STUB)
        gh.chmod(0o755)
        self.env = mock.patch.dict(os.environ, {"PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
                                  "STUB_ROOT": str(self.base), "STUB_MODE": "ok"})
        self.env.start()
        self.addCleanup(self.env.stop)
        built = tool.build(self.root, REPO, self.base / "out")
        self.asset = Path(built["zip"])
        self.manifest = Path(built["manifest"])

    def git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.root, stderr=subprocess.PIPE).decode()

    def calls(self):
        path = self.base / "gh.log"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def assert_no_publish(self):
        self.assertFalse(any(c[0] == "release" for c in self.calls()), self.calls())

    def refresh_hash(self):
        manifest = json.loads(self.manifest.read_text())
        data = self.asset.read_bytes()
        manifest["asset"].update(size=len(data), sha256=hashlib.sha256(data).hexdigest())
        self.manifest.write_text(json.dumps(manifest))

    def rewrite_zip(self, change):
        with zipfile.ZipFile(self.asset) as archive:
            entries = [(info, archive.read(info)) for info in archive.infolist()]
        with zipfile.ZipFile(self.asset, "w") as archive:
            for index, (info, data) in enumerate(entries):
                info, data = change(index, info, data)
                archive.writestr(info, data)
        self.refresh_hash()

    def test_deterministic_exact_source_only_and_full_paths(self):
        plugin = self.root / tool.PREFIX
        for name in ("config.json", "runtime/usage.db", "__pycache__/secret.pyc", ".git/config", ".DS_Store"):
            path = plugin / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("PRIVATE-USAGE-TOKEN")
        for name in tool.NAMES:
            path = self.root / name
            os.utime(path, (1800000000, 1800000000))
            path.chmod(0o600)
        result = tool.build(self.root, REPO, self.base / "again")
        self.assertEqual(self.asset.read_bytes(), Path(result["zip"]).read_bytes())
        self.assertEqual(self.manifest.read_bytes(), Path(result["manifest"]).read_bytes())
        self.assertTrue(Path(result["zip"]).is_absolute())
        with zipfile.ZipFile(self.asset) as archive:
            self.assertEqual(archive.namelist(), list(tool.NAMES))
            self.assertIn(tool.PREFIX + "release_unpack.py", archive.namelist())
            self.assertNotIn("release_tool.py", archive.namelist())
            self.assertFalse(any("skills/github-release/" in name or name.startswith(".zcode/")
                                 for name in archive.namelist()))
        self.assertNotIn(b"PRIVATE-USAGE-TOKEN", self.asset.read_bytes())

    def test_official_catalog_url_hash_path_and_internal_market_stays_local(self):
        catalog = json.loads((self.asset.parent / "marketplace-release.json").read_text())
        entry = catalog["plugins"][0]
        digest = json.loads(self.manifest.read_text())["asset"]["sha256"]
        self.assertEqual(entry["version"], "1.2.3")
        self.assertEqual(entry["source"], {"source": "url", "type": "zip",
            "url": f"https://github.com/{REPO}/releases/download/v1.2.3/{self.asset.name}",
            "sha256": digest, "path": "plugins/" + tool.PLUGIN, "stripRoot": False})
        with zipfile.ZipFile(self.asset) as archive:
            self.assertEqual(archive.read("LICENSE"), (self.root / "LICENSE").read_bytes())
            self.assertEqual(json.loads(archive.read("marketplace.json"))["plugins"][0]["source"],
                             "./plugins/" + tool.PLUGIN)
        self.assertEqual(json.loads((self.root / "marketplace.json").read_text())["plugins"][0]["source"],
                         "./plugins/" + tool.PLUGIN)

    def test_existing_output_never_overwritten(self):
        before = self.asset.read_bytes()
        with self.assertRaisesRegex(tool.ReleaseError, "already exists"):
            tool.build(self.root, REPO, self.asset.parent)
        self.assertEqual(before, self.asset.read_bytes())

    def test_versions_and_identity_mismatch(self):
        for version in ("1.2", "01.2.3", "1.2.3-beta", "v1.2.3", "1.2.4"):
            with self.subTest(version=version), self.assertRaises(tool.ReleaseError):
                tool.build(self.root, REPO, self.base / ("bad-" + version), version)
        path = self.root / tool.PREFIX / ".claude-plugin/plugin.json"
        value = json.loads(path.read_text())
        value["version"] = "1.2.4"
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(tool.ReleaseError, "manifests"):
            tool.build(self.root, REPO, self.base / "mismatch")

    def test_marketplace_mismatch_and_sync_drift(self):
        path = self.root / "marketplace.json"
        value = json.loads(path.read_text())
        value["plugins"][0]["version"] = "1.2.4"
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(tool.ReleaseError, "marketplace"):
            tool.build(self.root, REPO, self.base / "market-bad")
        value["plugins"][0]["version"] = "1.2.3"
        path.write_text(json.dumps(value))
        (self.root / "runtime_lock.py").write_text("drift")
        with self.assertRaisesRegex(tool.ReleaseError, "copy drift"):
            tool.build(self.root, REPO, self.base / "drift")

    def test_symlinks_anywhere_rejected(self):
        (self.root / tool.PREFIX / "hidden-config").symlink_to(self.manifest)
        with self.assertRaisesRegex(tool.ReleaseError, "symlink"):
            tool.build(self.root, REPO, self.base / "symlink")

    def test_asset_hash_and_repository_mismatch(self):
        with self.assertRaisesRegex(tool.ReleaseError, "repository"):
            tool.verify(self.asset, self.manifest, "other/repo")
        with self.asset.open("ab") as stream:
            stream.write(b"corrupt")
        with self.assertRaisesRegex(tool.ReleaseError, "hash"):
            tool.verify(self.asset, self.manifest, REPO)

    def test_unsafe_zip_paths_duplicates_and_secret_rejected(self):
        original = self.asset.read_bytes()
        for name in ("../secret", "/absolute", "plugins\\secret", "marketplace.json",
                     tool.PREFIX + "runtime/usage.db"):
            with self.subTest(name=name):
                self.asset.write_bytes(original)
                self.rewrite_zip(lambda i, info, data: (zipfile.ZipInfo(name), data) if i == 0 else (info, data))
                with self.assertRaises(tool.ReleaseError):
                    tool.verify(self.asset, self.manifest, REPO)

    def test_zip_symlink_and_unsupported_compression_rejected(self):
        def symlink(index, info, data):
            if index == 0:
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
            return info, data
        original = self.asset.read_bytes()
        self.rewrite_zip(symlink)
        with self.assertRaisesRegex(tool.ReleaseError, "non-regular|symlink/device"):
            tool.verify(self.asset, self.manifest, REPO)
        self.asset.write_bytes(original)
        def unsupported(index, info, data):
            info.compress_type = zipfile.ZIP_BZIP2
            return info, data
        self.rewrite_zip(unsupported)
        with self.assertRaisesRegex(tool.ReleaseError, "unsupported"):
            tool.verify(self.asset, self.manifest, REPO)

    def test_zip_manifest_version_corruption_with_valid_outer_hash_rejected(self):
        def mismatch(index, info, data):
            if info.filename.endswith("/.zcode-plugin/plugin.json"):
                value = json.loads(data)
                value["version"] = "1.2.4"
                data = json.dumps(value).encode()
            return info, data
        self.rewrite_zip(mismatch)
        with self.assertRaisesRegex(tool.ReleaseError, "manifests.*disagree"):
            tool.verify(self.asset, self.manifest, REPO)

    def test_verify_reuses_unpack_for_invalid_mcp_and_missing_hook_references(self):
        original = self.asset.read_bytes()
        cases = [(".mcp.json", b"not JSON", "Expecting value"),
                 ("hooks/hooks.json", json.dumps({"hooks": {"SessionStart": [{"hooks": [{
                     "command": "python3", "args": ["${ZCODE_PLUGIN_ROOT}/hooks/missing.py"]}]}]}}).encode(),
                  "missing or unsafe declared")]
        for name, replacement, message in cases:
            with self.subTest(name=name):
                self.asset.write_bytes(original)
                self.rewrite_zip(lambda i, info, data: (info, replacement)
                                 if info.filename == tool.PREFIX + name else (info, data))
                with self.assertRaisesRegex(tool.ReleaseError, message):
                    tool.verify(self.asset, self.manifest, REPO)
                self.assert_no_publish()

    def test_verify_rejects_duplicate_json_keys_even_with_correct_outer_hash(self):
        self.rewrite_zip(lambda i, info, data: (info, b'{"mcpServers":{},"mcpServers":{}}')
                         if info.filename == tool.PREFIX + ".mcp.json" else (info, data))
        with self.assertRaisesRegex(tool.ReleaseError, "duplicate JSON key"):
            tool.verify(self.asset, self.manifest, REPO)

    def test_verify_rejects_json_size_and_high_compression_ratio(self):
        original = self.asset.read_bytes()
        oversized = json.dumps({"padding": "x" * tool.MAX_MANIFEST}).encode()
        self.rewrite_zip(lambda i, info, data: (info, oversized)
                         if info.filename == tool.PREFIX + ".mcp.json" else (info, data))
        with self.assertRaisesRegex(tool.ReleaseError, "JSON exceeds"):
            tool.verify(self.asset, self.manifest, REPO)
        self.asset.write_bytes(original)
        def bomb(index, info, data):
            if info.filename == tool.PREFIX + "README.md":
                info.compress_type = zipfile.ZIP_DEFLATED
                data = b"x" * (2 * 1024 * 1024)
            return info, data
        self.rewrite_zip(bomb)
        with self.assertRaisesRegex(tool.ReleaseError, "compression ratio"):
            tool.verify(self.asset, self.manifest, REPO)

    def test_verify_rejects_boolean_numeric_manifest_equivalence(self):
        def bool_identity(index, info, data):
            if info.filename.endswith("/.zcode-plugin/plugin.json") or info.filename.endswith("/.claude-plugin/plugin.json"):
                value = json.loads(data)
                value["testIdentity"] = True if "/.zcode-plugin/" in info.filename else 1
                data = json.dumps(value).encode()
            return info, data
        self.rewrite_zip(bool_identity)
        with self.assertRaisesRegex(tool.ReleaseError, "plugin manifests disagree"):
            tool.verify(self.asset, self.manifest, REPO)

    def test_runtime_helper_drift_rejected_and_whitelist_matches_unpacker(self):
        spec = importlib.util.spec_from_file_location("release_unpack_contract_test", ROOT / "release_unpack.py")
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        self.assertEqual(set(tool.NAMES), set(helper.ALLOWED_FILES))
        (self.root / tool.PREFIX / "release_unpack.py").write_text("drift")
        with self.assertRaisesRegex(tool.ReleaseError, "runtime copy drift: release_unpack.py"):
            tool.build(self.root, REPO, self.base / "helper-drift")

    def test_verify_uses_trusted_helper_not_zip_code_and_cleans_temporary_output(self):
        marker = self.base / "untrusted-code-executed"
        malicious = f'from pathlib import Path\nPath({str(marker)!r}).write_text("executed")\n'.encode()
        self.rewrite_zip(lambda i, info, data: (info, malicious)
                         if info.filename == tool.PREFIX + "release_unpack.py"
                         else (info, data))
        original = tool.tempfile.TemporaryDirectory
        directories = []
        def owned_directory(*args, **kwargs):
            temporary = original(*args, **kwargs)
            directories.append(Path(temporary.name))
            return temporary
        with mock.patch.object(tool.tempfile, "TemporaryDirectory", side_effect=owned_directory):
            tool.verify(self.asset, self.manifest, REPO)
            self.rewrite_zip(lambda i, info, data: (info, b"not JSON")
                             if info.filename == tool.PREFIX + ".mcp.json" else (info, data))
            with self.assertRaises(tool.ReleaseError):
                tool.verify(self.asset, self.manifest, REPO)
        self.assertEqual(len(directories), 2)
        self.assertTrue(all(not directory.exists() for directory in directories))
        self.assertFalse(marker.exists())
        self.assertTrue(self.asset.exists())

    def test_oversize_limits_fail_closed(self):
        with mock.patch.object(tool, "MAX_ASSET", 10), self.assertRaisesRegex(tool.ReleaseError, "oversize"):
            tool.verify(self.asset, self.manifest, REPO)
        with mock.patch.object(tool, "MAX_MANIFEST", 10), self.assertRaisesRegex(tool.ReleaseError, "oversize"):
            tool.verify(self.asset, self.manifest, REPO)
        with mock.patch.object(tool, "MAX_TOTAL", 10), self.assertRaisesRegex(tool.ReleaseError, "oversize"):
            tool.verify(self.asset, self.manifest, REPO)

    def test_dry_run_reports_only_plan(self):
        plan = tool.publish(self.root, REPO, self.asset, self.manifest)
        self.assertTrue(plan["dryRun"])
        self.assertEqual(plan["repository"], REPO)
        self.assertEqual(plan["tag"], "v1.2.3")
        self.assert_no_publish()

    def test_publish_requires_both_explicit_approvals(self):
        for confirm in (None, "origin/v1.2.3", REPO + "/v1.2.4"):
            with self.subTest(confirm=confirm), self.assertRaisesRegex(tool.ReleaseError, "--confirm"):
                tool.publish(self.root, REPO, self.asset, self.manifest, True, confirm)
        self.assert_no_publish()

    def test_dirty_and_untracked_relevant_git_never_publish(self):
        (self.root / "runtime_lock.py").write_text("dirty")
        with self.assertRaises(tool.ReleaseError):
            tool.publish(self.root, REPO, self.asset, self.manifest, True, REPO + "/v1.2.3")
        self.git("checkout", "--", "runtime_lock.py")
        (self.root / tool.PREFIX / "local.py").write_text("untracked")
        with self.assertRaisesRegex(tool.ReleaseError, "dirty or untracked"):
            tool.publish(self.root, REPO, self.asset, self.manifest)
        self.assert_no_publish()

    def test_noauth_readonly_tag_mismatch_and_existing_draft_never_publish(self):
        for mode in ("noauth", "readonly", "badtag", "exists"):
            with self.subTest(mode=mode), mock.patch.dict(os.environ, {"STUB_MODE": mode}):
                with self.assertRaises(tool.ReleaseError):
                    tool.publish(self.root, REPO, self.asset, self.manifest, True, REPO + "/v1.2.3")
        self.assert_no_publish()

    def test_explicit_remote_not_automatically_origin(self):
        self.git("remote", "remove", "writable")
        with self.assertRaisesRegex(tool.ReleaseError, "configured GitHub remote"):
            tool.publish(self.root, REPO, self.asset, self.manifest)
        self.assertEqual(self.calls(), [])

    def test_stub_publish_draft_upload_verify_then_publish(self):
        plan = tool.publish(self.root, REPO, self.asset, self.manifest, True, REPO + "/v1.2.3")
        self.assertFalse(plan["dryRun"])
        calls = self.calls()
        write_positions = [i for i, c in enumerate(calls) if c[0] == "release"]
        self.assertEqual([calls[i][1] for i in write_positions], ["create", "upload", "edit"])
        self.assertIn("--draft", calls[write_positions[0]])
        self.assertIn("--verify-tag", calls[write_positions[0]])
        between = calls[write_positions[1] + 1:write_positions[2]]
        self.assertEqual(sum("/releases/assets/" in c[1] for c in between), 2)
        self.assertIn("--draft=false", calls[write_positions[2]])
        self.assertTrue(calls[-1][1].endswith("/releases/tags/v1.2.3"))
        self.assertFalse(any("--clobber" in c or "--force" in c for c in calls))

    def test_failed_remote_asset_verification_leaves_draft(self):
        with mock.patch.dict(os.environ, {"STUB_MODE": "badhash"}):
            with self.assertRaisesRegex(tool.ReleaseError, "hash disagrees"):
                tool.publish(self.root, REPO, self.asset, self.manifest, True, REPO + "/v1.2.3")
        self.assertEqual([c[1] for c in self.calls() if c[0] == "release"], ["create", "upload"])
        self.assertTrue(json.loads((self.base / "gh-state.json").read_text())["draft"])

    def test_malformed_manifest_objects_rejected(self):
        for value in ([], {"repository": REPO, "version": "1.2.3", "tag": "v1.2.3", "schemaVersion": 1,
                          "pluginId": tool.PLUGIN_ID, "asset": []}):
            self.manifest.write_text(json.dumps(value))
            with self.subTest(value=value), self.assertRaises(tool.ReleaseError):
                tool.verify(self.asset, self.manifest, REPO)

    def test_encrypted_zip_flag_rejected(self):
        data = bytearray(self.asset.read_bytes())
        local = data.index(b"PK\x03\x04")
        central = data.index(b"PK\x01\x02")
        for position in (local + 6, central + 8):
            flags = int.from_bytes(data[position:position + 2], "little") | 1
            data[position:position + 2] = flags.to_bytes(2, "little")
        self.asset.write_bytes(data)
        self.refresh_hash()
        with self.assertRaisesRegex(tool.ReleaseError, "encrypted"):
            tool.verify(self.asset, self.manifest, REPO)

    def test_missing_or_wrong_local_tag_never_publish(self):
        self.git("tag", "-d", "v1.2.3")
        with self.assertRaises(tool.ReleaseError):
            tool.publish(self.root, REPO, self.asset, self.manifest)
        self.assertEqual(self.calls(), [])

    def test_source_must_be_committed_at_head(self):
        path = self.root / tool.PREFIX / "token_rate.py"
        path.write_text("uncommitted source\n")
        built = tool.build(self.root, REPO, self.base / "dirty-out")
        with self.assertRaisesRegex(tool.ReleaseError, "dirty or untracked"):
            tool.publish(self.root, REPO, Path(built["zip"]), Path(built["manifest"]), True, REPO + "/v1.2.3")
        self.assertEqual(self.calls(), [])

    def test_publish_postcondition_checked(self):
        with mock.patch.dict(os.environ, {"STUB_MODE": "badpublish"}):
            with self.assertRaisesRegex(tool.ReleaseError, "published release state"):
                tool.publish(self.root, REPO, self.asset, self.manifest, True, REPO + "/v1.2.3")

    def test_cli_failure_reports_nonzero_without_side_effect(self):
        with contextlib.redirect_stderr(__import__("io").StringIO()):
            code = tool.main(["publish", "--root", str(self.root), "--repo", REPO,
                              "--zip", str(self.asset), "--manifest", str(self.manifest), "--publish"])
        self.assertEqual(code, 1)
        self.assertEqual(self.calls(), [])


if __name__ == "__main__":
    unittest.main()
