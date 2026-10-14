# -*- coding: utf-8 -*-
"""Build and unpack real repository sources without Git, network or installed runtime access."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "release-fixture/zcode-token-usage-statusbar"
HEAD = "0123456789abcdef0123456789abcdef01234567"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    # Importing fixture copies must not introduce __pycache__ into the release tree.
    with mock.patch.object(sys, "dont_write_bytecode", True):
        spec.loader.exec_module(module)
    return module


class ReleaseIntegrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.root = self.base / "repository"
        self.root.mkdir()
        home = self.base / "home"
        home.mkdir()
        # No real HOME, credentials, git configuration, cache or external command is available.
        for patcher in (
                mock.patch.object(sys, "dont_write_bytecode", True),
                mock.patch.object(tempfile, "tempdir", str(self.base)),
                mock.patch.dict(os.environ, {"HOME": str(home), "USERPROFILE": str(home),
                    "XDG_CONFIG_HOME": str(home), "XDG_CACHE_HOME": str(home),
                    "TMPDIR": str(self.base), "PYTHONDONTWRITEBYTECODE": "1", "PATH": ""}, clear=True),
                mock.patch.object(subprocess, "Popen", side_effect=AssertionError("external command forbidden")),
                mock.patch.object(socket, "socket", side_effect=AssertionError("network forbidden")),
                mock.patch.object(socket, "create_connection", side_effect=AssertionError("network forbidden"))):
            patcher.start()
            self.addCleanup(patcher.stop)
        source_tool = load_module("integration_source_release_tool", ROOT / "release_tool.py")
        self.source_bytes = {}
        # Explicit whitelist only: never copy repository state, local usage or cached files.
        for name in source_tool.NAMES:
            if name.startswith(source_tool.PREFIX):
                relative = name[len(source_tool.PREFIX):]
                if relative in source_tool.SYNC_FILES:
                    source = ROOT / relative
                else:
                    source = ROOT / name
            else:
                source = ROOT / name
            self.assertFalse(source.is_symlink(), str(source))
            content = source.read_bytes()
            self.source_bytes[name] = content
            destination = self.root / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
        for name in source_tool.SYNC_FILES:
            (self.root / name).write_bytes(self.source_bytes[source_tool.PREFIX + name])
        (self.root / "release_tool.py").write_bytes((ROOT / "release_tool.py").read_bytes())
        self.tool = load_module("integration_fixture_release_tool", self.root / "release_tool.py")
        self.helper = load_module("integration_fixture_release_unpack", self.root / "release_unpack.py")
        self.plugin_manifest = json.loads(self.source_bytes[self.tool.PREFIX + ".zcode-plugin/plugin.json"])
        self.version = self.plugin_manifest["version"]
        self.tag = "v" + self.version
        self.built = self.tool.build(self.root, REPOSITORY, self.base / "release", self.version)
        self.archive = Path(self.built["zip"])
        self.manifest = Path(self.built["manifest"])
        self.catalog = Path(self.built["catalog"])
        self.output = self.base / "unpacked"
        self.calls = []

    def stub_run(self, root, *args, **kwargs):
        self.assertEqual(Path(root).resolve(), self.root)
        self.assertEqual(kwargs, {})
        self.calls.append(args)
        if args == ("git", "rev-parse", "--show-toplevel"):
            return (str(self.root) + "\n").encode()
        if args in (("git", "rev-parse", "--verify", "HEAD^{commit}"),
                    ("git", "rev-parse", "--verify", "refs/tags/" + self.tag + "^{commit}")):
            return (HEAD + "\n").encode()
        if args == ("git", "status", "--porcelain", "--untracked-files=all", "--", *self.tool.RELEVANT):
            return b""
        if len(args) == 3 and args[:2] == ("git", "show") and args[2].startswith("HEAD:"):
            return self.source_bytes[args[2][len("HEAD:"):]]
        if args == ("git", "remote", "-v"):
            return ("origin https://github.com/" + REPOSITORY + ".git (fetch)\n").encode()
        if args == ("gh", "auth", "status", "--hostname", "github.com"):
            return b""
        if args[:2] == ("gh", "api"):
            self.assertIn("--method", args)
            self.assertEqual(args[args.index("--method") + 1], "GET")
            self.assertIn("--hostname", args)
            self.assertEqual(args[args.index("--hostname") + 1], "github.com")
            endpoint = args[2]
            if endpoint == "repos/" + REPOSITORY:
                value = {"full_name": REPOSITORY, "permissions": {"push": True}}
            elif endpoint == "repos/" + REPOSITORY + "/git/ref/tags/" + self.tag:
                value = {"object": {"type": "commit", "sha": HEAD}}
            elif endpoint == "repos/" + REPOSITORY + "/releases?per_page=100":
                self.assertIn("--paginate", args)
                self.assertIn("--slurp", args)
                value = [[]]
            else:
                self.fail("unexpected read endpoint: " + repr(args))
            return json.dumps(value).encode()
        self.fail("unexpected or mutating command: " + repr(args))

    def test_real_source_build_verify_unpack_and_derived_catalog_agree(self):
        self.assertEqual(self.helper.ALLOWED_FILES, frozenset(self.tool.NAMES))
        self.assertEqual(self.helper.REQUIRED_FILES, self.helper.ALLOWED_FILES)
        self.assertIn(self.tool.PREFIX + "release_unpack.py", self.source_bytes)
        self.assertNotIn("release_tool.py", self.source_bytes)
        self.assertFalse(any("skills/github-release/" in name or name.startswith(".zcode/")
                             for name in self.source_bytes))
        self.assertEqual(self.helper.MAX_ARCHIVE, self.tool.MAX_ASSET)
        self.assertEqual(self.helper.MAX_JSON, self.tool.MAX_MANIFEST)
        self.assertEqual(self.helper.MAX_FILE, self.tool.MAX_FILE)
        self.assertEqual(self.helper.MAX_TOTAL, self.tool.MAX_TOTAL)
        manifest = self.tool.verify(self.archive, self.manifest, REPOSITORY)
        payload = self.archive.read_bytes()
        self.assertEqual(manifest, {"schemaVersion": 1, "repository": REPOSITORY,
            "version": self.version, "tag": self.tag, "pluginId": self.helper.PLUGIN_ID,
            "asset": {"name": self.tool.zip_name(self.version), "size": len(payload),
                      "sha256": hashlib.sha256(payload).hexdigest()}})
        for key, path in (("zip", self.archive), ("manifest", self.manifest), ("catalog", self.catalog)):
            self.assertTrue(path.is_absolute())
            self.assertEqual(self.built[key], str(path.resolve()))
        with zipfile.ZipFile(self.archive) as archive:
            self.assertEqual(archive.namelist(), list(self.tool.NAMES))
            self.assertEqual({name: archive.read(name) for name in archive.namelist()}, self.source_bytes)
        result = self.helper.unpack(self.archive, self.manifest, REPOSITORY, self.version, self.output)
        self.assertEqual(result, {"ok": True, "marketplaceRoot": str(self.output.resolve()),
            "pluginRoot": str((self.output / "plugins" / self.tool.PLUGIN).resolve()), "version": self.version})
        files = {path.relative_to(self.output).as_posix(): path.read_bytes()
                 for path in self.output.rglob("*") if path.is_file()}
        directories = {path.relative_to(self.output).as_posix()
                       for path in self.output.rglob("*") if path.is_dir()}
        self.assertEqual(files, self.source_bytes)
        self.assertEqual(directories, self.helper.ALLOWED_DIRS)
        self.assertFalse(any(path.is_symlink() for path in self.output.rglob("*")))
        plugin_root = Path(result["pluginRoot"])
        for name in (".zcode-plugin/plugin.json", ".claude-plugin/plugin.json"):
            self.assertEqual(json.loads((plugin_root / name).read_bytes()), self.plugin_manifest)
        market = json.loads((self.output / "marketplace.json").read_bytes())
        self.assertEqual(market, json.loads(self.source_bytes["marketplace.json"]))
        self.assertEqual(market["plugins"][0]["version"], result["version"])
        self.assertEqual((self.output / market["plugins"][0]["source"]).resolve(), plugin_root)
        expected_catalog = copy.deepcopy(market)
        expected_catalog["plugins"][0]["source"] = {
            "source": "url", "type": "zip", "url": "https://github.com/" + REPOSITORY +
            "/releases/download/" + self.tag + "/" + self.archive.name,
            "sha256": manifest["asset"]["sha256"], "path": "plugins/" + self.tool.PLUGIN,
            "stripRoot": False}
        self.assertEqual(json.loads(self.catalog.read_bytes()), expected_catalog)
        self.assertEqual((self.root / "marketplace.json").read_bytes(), self.source_bytes["marketplace.json"])
        self.assertNotIn("marketplace-release.json", files)
        for name in self.tool.SYNC_FILES:
            self.assertEqual((self.root / name).read_bytes(), (plugin_root / name).read_bytes())
        self.assertEqual(self.calls, [])

    def test_root_tool_uses_adjacent_real_helper_for_complete_release(self):
        script = self.root / "release_tool.py"
        adjacent_helper = script.with_name("release_unpack.py")
        self.assertEqual(adjacent_helper.read_bytes(), (ROOT / "release_unpack.py").read_bytes())
        root_tool = load_module("integration_fixture_root_release_tool", script)
        self.assertEqual(root_tool.verify(self.archive, self.manifest, REPOSITORY),
                         self.tool.verify(self.archive, self.manifest, REPOSITORY))
        self.assertFalse(any(path.name == "__pycache__" for path in self.root.rglob("*")))
        self.assertFalse(self.output.exists())
        self.assertEqual(self.calls, [])

    def test_tool_and_helper_share_strict_json_resource_and_ratio_validation(self):
        original_manifest = self.manifest.read_bytes()
        cases = ("invalid-json", "oversize-json", "missing-hook", "duplicate-json",
                 "manifest-types", "compression-ratio", "missing-runtime", "missing-runtime-helper")
        for case in cases:
            with self.subTest(case=case):
                data = self.source_bytes.copy()
                if case == "invalid-json":
                    data[self.tool.PREFIX + ".mcp.json"] = b"{bad"
                elif case == "oversize-json":
                    data[self.tool.PREFIX + ".mcp.json"] = b" " * self.helper.MAX_JSON + b"{}"
                elif case == "missing-hook":
                    data[self.tool.PREFIX + "hooks/hooks.json"] = json.dumps({
                        "hooks": {"args": ["${ZCODE_PLUGIN_ROOT}/missing.py"]}}).encode()
                elif case == "duplicate-json":
                    data["marketplace.json"] = data["marketplace.json"].replace(
                        b"{", b'{"name":"zusage-marketplace",', 1)
                elif case == "manifest-types":
                    for relative, value in ((".zcode-plugin/plugin.json", True),
                                            (".claude-plugin/plugin.json", 1)):
                        manifest = dict(self.plugin_manifest, enabled=value)
                        data[self.tool.PREFIX + relative] = json.dumps(manifest).encode()
                elif case == "compression-ratio":
                    data[self.tool.PREFIX + "README.md"] = b"0" * (2 * 1024 * 1024)
                else:
                    missing = "patch_install.py" if case == "missing-runtime" else "release_unpack.py"
                    del data[self.tool.PREFIX + missing]
                compression = zipfile.ZIP_DEFLATED if case == "compression-ratio" else zipfile.ZIP_STORED
                with zipfile.ZipFile(self.archive, "w", compression=compression) as archive:
                    for name in self.tool.NAMES:
                        if name in data:
                            archive.writestr(name, data[name])
                manifest = json.loads(original_manifest)
                payload = self.archive.read_bytes()
                manifest["asset"].update(size=len(payload), sha256=hashlib.sha256(payload).hexdigest())
                self.manifest.write_text(json.dumps(manifest), encoding="utf-8")
                with self.assertRaises(self.tool.ReleaseError):
                    self.tool.verify(self.archive, self.manifest, REPOSITORY)
                with self.assertRaises((ValueError, zipfile.BadZipFile)):
                    self.helper.unpack(self.archive, self.manifest, REPOSITORY, self.version, self.output)
                self.assertFalse(self.output.exists())
                self.assertEqual(self.calls, [])

    def test_shared_validator_never_executes_helpers_from_release_archive(self):
        data = self.source_bytes.copy()
        data[self.tool.PREFIX + "release_unpack.py"] = b'raise AssertionError("archive helper executed")\n'
        with zipfile.ZipFile(self.archive, "w", compression=zipfile.ZIP_STORED) as archive:
            for name in self.tool.NAMES:
                archive.writestr(name, data[name])
        manifest = json.loads(self.manifest.read_bytes())
        payload = self.archive.read_bytes()
        manifest["asset"].update(size=len(payload), sha256=hashlib.sha256(payload).hexdigest())
        self.manifest.write_text(json.dumps(manifest), encoding="utf-8")
        self.assertEqual(self.tool.verify(self.archive, self.manifest, REPOSITORY), manifest)
        self.helper.unpack(self.archive, self.manifest, REPOSITORY, self.version, self.output)
        self.assertEqual((self.output / self.tool.PREFIX / "release_unpack.py").read_bytes(),
                         data[self.tool.PREFIX + "release_unpack.py"])
        self.assertEqual(self.calls, [])

    def test_repeat_build_is_deterministic_and_excludes_local_state(self):
        plugin = self.root / self.tool.PREFIX
        for name in ("config.json", "usage.db", "runtime.lock", "__pycache__/private.pyc", ".git/config"):
            path = plugin / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"isolated local state must not ship")
        again = self.tool.build(self.root, REPOSITORY, self.base / "release-again", self.version)
        for key in ("zip", "manifest", "catalog"):
            self.assertEqual(Path(again[key]).read_bytes(), Path(self.built[key]).read_bytes())
        self.helper.unpack(Path(again["zip"]), Path(again["manifest"]),
                           REPOSITORY, self.version, self.output)
        self.assertEqual({path.relative_to(self.output).as_posix() for path in self.output.rglob("*")
                          if path.is_file()}, set(self.tool.NAMES))

    def test_clean_fixture_publish_preflight_is_read_only_and_returns_plan(self):
        before = {path.relative_to(self.base).as_posix(): path.read_bytes()
                  for path in self.base.rglob("*") if path.is_file()}
        with mock.patch.object(self.tool, "run", side_effect=self.stub_run):
            plan = self.tool.publish(self.root, REPOSITORY, self.archive, self.manifest)
        self.assertTrue(plan["dryRun"])
        self.assertEqual((plan["repository"], plan["tag"], plan["commit"]), (REPOSITORY, self.tag, HEAD))
        self.assertEqual((plan["zip"], plan["manifest"]), (str(self.archive), str(self.manifest)))
        self.assertEqual([command[:3] for command in plan["commands"]],
                         [["gh", "release", "create"], ["gh", "release", "upload"],
                          ["gh", "release", "edit"]])
        self.assertIn("--draft", plan["commands"][0])
        self.assertIn("--verify-tag", plan["commands"][0])
        self.assertFalse(any("--force" in command or "--clobber" in command for command in plan["commands"]))
        self.assertTrue(any(call[:2] == ("gh", "api") for call in self.calls))
        self.assertFalse(any(call[:2] == ("gh", "release") for call in self.calls))
        self.assertEqual(before, {path.relative_to(self.base).as_posix(): path.read_bytes()
                                 for path in self.base.rglob("*") if path.is_file()})
        self.assertFalse((self.root / ".git").exists())
        self.assertEqual(list((self.base / "home").iterdir()), [])

    def test_dirty_fixture_preflight_stops_before_github_or_publish(self):
        def dirty_run(root, *args):
            if args[:2] == ("git", "status"):
                self.calls.append(args)
                return b"?? plugins/zcode-token-usage-statusbar/local-state.json\n"
            return self.stub_run(root, *args)

        with mock.patch.object(self.tool, "run", side_effect=dirty_run):
            with self.assertRaisesRegex(self.tool.ReleaseError, "dirty or untracked"):
                self.tool.publish(self.root, REPOSITORY, self.archive, self.manifest)
        self.assertTrue(self.calls)
        self.assertFalse(any(call[0] == "gh" for call in self.calls))
        self.assertFalse(self.output.exists())

    def test_explicit_publish_without_confirmation_stops_before_preflight(self):
        with mock.patch.object(self.tool, "run", side_effect=self.stub_run):
            with self.assertRaisesRegex(self.tool.ReleaseError, "--confirm"):
                self.tool.publish(self.root, REPOSITORY, self.archive, self.manifest, approved=True)
        self.assertEqual(self.calls, [])

    def test_built_asset_tamper_and_expected_source_mismatch_never_extract(self):
        with self.assertRaises(self.tool.ReleaseError):
            self.tool.verify(self.archive, self.manifest, "other/repository")
        with self.assertRaises(self.helper.UnpackError):
            self.helper.unpack(self.archive, self.manifest, "other/repository", self.version, self.output)
        with self.assertRaises(self.helper.UnpackError):
            self.helper.unpack(self.archive, self.manifest, REPOSITORY, "0.0.0", self.output)
        payload = bytearray(self.archive.read_bytes())
        payload[50] ^= 1
        self.archive.write_bytes(payload)
        with self.assertRaisesRegex(self.tool.ReleaseError, "hash"):
            self.tool.verify(self.archive, self.manifest, REPOSITORY)
        with self.assertRaisesRegex(self.helper.UnpackError, "sha256"):
            self.helper.unpack(self.archive, self.manifest, REPOSITORY, self.version, self.output)
        self.assertFalse(self.output.exists())
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
