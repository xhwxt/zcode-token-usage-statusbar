# -*- coding: utf-8 -*-
"""R1 的真实 Node 验收与 Python 同步集成；只使用临时 HOME/库存/运行时。"""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "zcode-token-usage-statusbar"


@unittest.skipUnless(shutil.which("node"), "需要 Node 运行真实离线验收")
class RuntimeSyncGuardTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.home = self.root / "home"
        self.storage = self.home / ".zcode" / "cli" / "plugins"
        self.storage.mkdir(parents=True)
        self.runtime = self.root / "runtime"
        self.runtime.mkdir()
        self.plugin = self.root / "installed"
        (self.plugin / ".zcode-plugin").mkdir(parents=True)
        self.version = "1.1.0"
        self.write(self.plugin / ".zcode-plugin" / "plugin.json", {"name": "zcode-token-usage-statusbar", "version": self.version})
        spec = importlib.util.spec_from_file_location("sync_guard_installer", PLUGIN / "overlay_install.py")
        self.installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.installer)
        for name in self.installer.RUNTIME_FILES:
            (self.plugin / name).write_bytes(("new package:" + name).encode())
        for name in ("plugin-updater.cjs", "plugin-release.cjs", "plugin-storage.cjs", "runtime_lock.py"):
            shutil.copy2(ROOT / name, self.runtime / name)
        self.source = {"source": "github", "repo": "ByteRax/zcode-token-usage-statusbar", "ref": "plugin-releases"}
        self.write(self.storage / "known_marketplaces.json", {"marketplaces": [
            {"id": "zusage-marketplace", "source": self.source}]})
        repo = self.source["repo"]
        base = "https://github.com/" + repo + "/releases/download/v1.1.0/"
        archive = {"name": "zcode-token-usage-statusbar-v1.1.0.zip", "size": 11,
                   "state": "uploaded", "browser_download_url": base + "zcode-token-usage-statusbar-v1.1.0.zip"}
        manifest_asset = {"name": "release-manifest.json", "size": 400,
                          "state": "uploaded", "browser_download_url": base + "release-manifest.json"}
        release = {"repository": repo, "version": "1.1.0", "tag": "v1.1.0", "archive": archive,
                   "manifest": manifest_asset, "url": "https://github.com/" + repo + "/releases/tag/v1.1.0"}
        manifest = {"schemaVersion": 1, "repository": repo, "version": "1.1.0", "tag": "v1.1.0",
                    "pluginId": "zcode-token-usage-statusbar@zusage-marketplace", "asset": {
                        "name": archive["name"], "size": 11, "sha256": hashlib.sha256(b"release zip").hexdigest()}}
        self.record = {"id": manifest["pluginId"], "scope": "user", "version": self.version,
                       "installPath": str(self.plugin), "source": {"source": "url", "type": "zip",
                        "url": archive["browser_download_url"], "sha256": manifest["asset"]["sha256"],
                        "path": "plugins/zcode-token-usage-statusbar", "stripRoot": False}}
        self.pending = {"pendingSync": True, "pendingVersion": "1.0.0", "pendingPid": 0,
                        "pendingRelease": {"release": release, "manifest": manifest,
                            "channel": {"kind": "release", "repository": repo, "source": self.source.copy()}}}
        self.save_state()
        patchers = (mock.patch.object(self.installer, "PLUGIN_ROOT", self.plugin),
                    mock.patch.object(Path, "home", return_value=self.home),
                    mock.patch.dict(os.environ, {"HOME": str(self.home), "USERPROFILE": str(self.home),
                        "ZUSAGE_PLUGIN_RUNTIME": str(self.runtime), "PYTHONDONTWRITEBYTECODE": "1",
                        "PATH": os.environ.get("PATH", "")}, clear=True))
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def write(self, path, value):
        path.write_text(json.dumps(value), encoding="utf-8")

    def save_state(self):
        self.write(self.storage / "installed_plugins.json", {"plugins": [self.record]})
        self.write(self.runtime / "plugin-update.json", self.pending)

    def sync(self):
        with contextlib.redirect_stderr(io.StringIO()):
            return self.installer.sync_runtime(self.runtime)

    def assert_rejected(self):
        before = {p.name: p.read_bytes() for p in self.runtime.iterdir() if p.is_file() and p.name != "plugin-runtime.lock"}
        with self.assertRaises((RuntimeError, ValueError)):
            self.sync()
        after = {p.name: p.read_bytes() for p in self.runtime.iterdir() if p.is_file() and p.name != "plugin-runtime.lock"}
        self.assertEqual(after, before)

    def test_all_sync_entries_reject_wrong_release_without_copying_updater(self):
        self.record["version"] = "1.2.0"
        self.write(self.plugin / ".zcode-plugin" / "plugin.json", {"name": "zcode-token-usage-statusbar", "version": "1.2.0"})
        self.save_state()
        self.assert_rejected()
        pi = mock.Mock()
        with mock.patch.object(self.installer, "resolve_paths", return_value=(self.runtime, pi)), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            result = self.installer.run_hook()
        self.assertEqual(output.getvalue(), "")
        self.assertIn("自检失败", result["systemMessage"])
        pi.install.assert_not_called()
        for command in ("sync", "install"):
            with self.subTest(command=command), mock.patch.object(sys, "argv", ["overlay_install.py", command]), \
                    mock.patch.object(self.installer, "resolve_paths", return_value=(self.runtime, pi)), \
                    contextlib.redirect_stdout(io.StringIO()) as output, contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises((SystemExit, RuntimeError, ValueError)):
                    self.installer.main()
                self.assertEqual(output.getvalue(), "")
        self.assertEqual((self.runtime / "plugin-updater.cjs").read_bytes(), (ROOT / "plugin-updater.cjs").read_bytes())
        self.assertTrue(json.loads((self.runtime / "plugin-update.json").read_text())["pendingSync"])
        self.assertFalse((self.runtime / "overlay.js").exists())

    def test_live_pending_pid_blocks_even_with_valid_inventory(self):
        self.pending["pendingPid"] = os.getpid()
        self.save_state()
        self.assert_rejected()

    def test_legacy_release_pending_without_contract_is_rejected(self):
        self.pending = {"pendingSync": True}
        self.save_state()
        self.assert_rejected()

    def test_valid_pending_release_recovers_offline_without_catalog_or_cli(self):
        self.write(self.runtime / "config.json", {"plugin_auto_update": False})
        with mock.patch.object(subprocess, "Popen", wraps=subprocess.Popen) as spawn:
            result = self.sync()
        self.assertEqual(result["preserved"], [])
        self.assertEqual((self.runtime / "overlay.js").read_bytes(), (self.plugin / "overlay.js").read_bytes())
        self.assertTrue(spawn.called, "必须运行真实稳定 Node 验收入口")
        for call in spawn.call_args_list:
            self.assertEqual(call.args[0][1], "-e")
            self.assertIn("updater.verifyRuntimeSync", call.args[0][2])
            self.assertNotIn(str(self.plugin / "plugin-updater.cjs"), call.args[0])
        self.assertTrue(json.loads((self.runtime / "plugin-update.json").read_text())["pendingSync"])

    def test_registration_ref_or_inventory_hash_change_is_rejected(self):
        for change in ("ref", "hash", "path"):
            with self.subTest(change=change):
                if change == "ref":
                    source = dict(self.source, ref="different")
                    self.write(self.storage / "known_marketplaces.json", {"marketplaces": [
                        {"id": "zusage-marketplace", "source": source}]})
                elif change == "hash":
                    self.record["source"]["sha256"] = "0" * 64
                    self.save_state()
                else:
                    self.record["installPath"] = str(self.home)
                    self.save_state()
                self.assert_rejected()

    def test_pending_without_trusted_validator_never_uses_new_package_fallback(self):
        (self.runtime / "plugin-updater.cjs").unlink()
        shutil.copy2(ROOT / "plugin-updater.cjs", self.plugin / "plugin-updater.cjs")
        self.assert_rejected()

    def test_uncommitted_pending_version_cannot_sync_old_cache(self):
        self.record["version"] = "1.0.0"
        self.write(self.plugin / ".zcode-plugin" / "plugin.json", {"name": "zcode-token-usage-statusbar", "version": "1.0.0"})
        self.save_state()
        self.assert_rejected()

    def use_head_runtime(self):
        for name in ("plugin-updater.cjs", "plugin-release.cjs", "plugin-storage.cjs"):
            data = subprocess.check_output(["git", "-C", str(ROOT), "show", "HEAD:" + name])
            (self.runtime / name).write_bytes(data)
        self.write(self.runtime / "plugin-runtime.json", {
            "version": "1.0.0", "plugin_root": str(self.root / "old-installed"),
            "hashes": {name: hashlib.sha256((self.runtime / name).read_bytes()).hexdigest()
                       for name in self.installer.RUNTIME_FILES if (self.runtime / name).is_file()}})
        # Cache updater is deliberately not executable: it may be copied, never
        # required during migration, including rechecks after it replaces HEAD.
        (self.plugin / "plugin-updater.cjs").write_text(
            'throw new Error("new cache helper must not execute");', encoding="utf-8")

    def test_head_runtime_valid_release_pending_recovers_using_frozen_snapshot(self):
        self.use_head_runtime()
        with mock.patch.object(subprocess, "Popen", wraps=subprocess.Popen) as spawn:
            result = self.sync()
        self.assertIn("plugin-updater.cjs", result["changed"])
        self.assertEqual(result["preserved"], [])
        self.assertEqual((self.runtime / "plugin-updater.cjs").read_bytes(),
                         (self.plugin / "plugin-updater.cjs").read_bytes())
        self.assertTrue(json.loads((self.runtime / "plugin-update.json").read_text())["pendingSync"])
        commands = [call.args[0] for call in spawn.call_args_list]
        self.assertGreater(len(commands), len(result["changed"]))
        self.assertEqual(len({command[3] for command in commands}), 1,
                         "首末与每文件验收必须使用同一可信快照")
        self.assertTrue(all(command[1] == "-e" for command in commands))

    def test_head_runtime_valid_local_pending_recovers(self):
        self.use_head_runtime()
        self.source = {"source": "directory", "path": str(self.root / "marketplace")}
        self.write(self.storage / "known_marketplaces.json", {"marketplaces": [
            {"id": "zusage-marketplace", "source": self.source}]})
        self.pending["pendingRelease"] = None
        self.save_state()
        result = self.sync()
        self.assertEqual(result["preserved"], [])
        self.assertEqual((self.runtime / "overlay.js").read_bytes(),
                         (self.plugin / "overlay.js").read_bytes())

    def test_head_runtime_local_pending_pid_and_uncommitted_version_are_rejected(self):
        self.use_head_runtime()
        self.write(self.storage / "known_marketplaces.json", {"marketplaces": [
            {"id": "zusage-marketplace", "source": {"source": "directory", "path": str(self.root)}}]})
        self.pending["pendingRelease"] = None
        self.pending["pendingPid"] = os.getpid()
        self.save_state()
        self.assert_rejected()
        self.pending["pendingPid"] = 0
        self.pending["pendingVersion"] = self.version
        self.save_state()
        self.assert_rejected()

    def test_head_runtime_without_pending_can_upgrade(self):
        self.use_head_runtime()
        self.pending = {}
        self.save_state()
        self.assertEqual(self.sync()["preserved"], [])

    def test_head_runtime_wrong_release_version_rejected_before_writes(self):
        self.use_head_runtime()
        self.record["version"] = "1.2.0"
        self.write(self.plugin / ".zcode-plugin" / "plugin.json", {
            "name": "zcode-token-usage-statusbar", "version": "1.2.0"})
        self.save_state()
        self.assert_rejected()

    def test_head_runtime_wrong_hash_and_live_pid_rejected_before_writes(self):
        self.use_head_runtime()
        digest = self.record["source"]["sha256"]
        self.record["source"]["sha256"] = "0" * 64
        self.save_state()
        self.assert_rejected()
        self.record["source"]["sha256"] = digest
        self.pending["pendingPid"] = os.getpid()
        self.save_state()
        self.assert_rejected()

    def test_head_runtime_ref_root_manifest_and_missing_contract_rejected(self):
        self.use_head_runtime()
        self.record["installPath"] = str(self.home)
        self.save_state()
        self.assert_rejected()
        self.record["installPath"] = str(self.plugin)
        self.write(self.plugin / ".zcode-plugin" / "plugin.json", {
            "name": "different-plugin", "version": self.version})
        self.save_state()
        self.assert_rejected()
        self.write(self.plugin / ".zcode-plugin" / "plugin.json", {
            "name": "zcode-token-usage-statusbar", "version": self.version})
        self.write(self.storage / "known_marketplaces.json", {"marketplaces": [
            {"id": "zusage-marketplace", "source": dict(self.source, ref="different")}]})
        self.assert_rejected()
        self.write(self.storage / "known_marketplaces.json", {"marketplaces": [
            {"id": "zusage-marketplace", "source": self.source}]})
        self.pending["pendingRelease"] = None
        self.save_state()
        self.assert_rejected()

    def test_head_runtime_uses_storage_override_and_rejects_corrupt_storage_config(self):
        self.use_head_runtime()
        override = self.root / "custom-storage"
        target = override / "cli" / "plugins"
        target.parent.mkdir(parents=True)
        shutil.move(str(self.storage), str(target))
        with mock.patch.dict(os.environ, {"ZCODE_STORAGE_DIR": str(override)}):
            self.assertEqual(self.sync()["preserved"], [])
        # Fresh HEAD snapshot; even an environment override cannot hide a broken
        # lower-priority config, per the old runtime storage contract.
        self.use_head_runtime()
        config = self.home / ".zcode" / "cli" / "config.json"
        config.write_text("{broken", encoding="utf-8")
        with mock.patch.dict(os.environ, {"ZCODE_STORAGE_DIR": str(override)}):
            self.assert_rejected()

    def test_head_snapshot_recheck_rejects_identity_change_between_files(self):
        self.use_head_runtime()
        write = self.installer._atomic_write
        def change_after_loader(dst, data, src=None):
            write(dst, data, src)
            if dst.name == "inject-main.cjs":
                self.record["source"]["sha256"] = "0" * 64
                self.save_state()
        with mock.patch.object(self.installer, "_atomic_write", side_effect=change_after_loader):
            with self.assertRaisesRegex(ValueError, "安装结果与选定 Release 不一致"):
                self.sync()
        self.assertFalse((self.runtime / "overlay.js").exists())
        self.assertTrue((self.runtime / "plugin-runtime-pending.json").exists())

    def test_first_bootstrap_without_runtime_or_pending_retains_cache_verifier(self):
        for name in ("plugin-updater.cjs", "plugin-release.cjs", "plugin-storage.cjs"):
            (self.runtime / name).unlink()
            shutil.copy2(ROOT / name, self.plugin / name)
        self.pending = {}
        self.save_state()
        self.assertEqual(self.sync()["preserved"], [])


if __name__ == "__main__":
    unittest.main()
