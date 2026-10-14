# -*- coding: utf-8 -*-
"""插件运行时同步的隔离测试：不读取真实 asar，不写用户运行时目录。"""
import ast
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
import time
import unittest
from unittest import mock


INSTALLER_PATH = (Path(__file__).resolve().parents[1] / "plugins" /
                  "zcode-token-usage-statusbar" / "overlay_install.py")
spec = importlib.util.spec_from_file_location("plugin_overlay_install", str(INSTALLER_PATH))
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)
notice_spec = importlib.util.spec_from_file_location("restart_notice_fixture", str(INSTALLER_PATH.parent / "restart_notice.py"))
notice = importlib.util.module_from_spec(notice_spec)
notice_spec.loader.exec_module(notice)


class PluginRuntimeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        notice_patcher = mock.patch.dict(sys.modules, {"restart_notice": notice})
        notice_patcher.start()
        self.addCleanup(notice_patcher.stop)
        self.plugin = self.root / "plugin"
        self.runtime = self.root / "runtime"
        self.timestamp = 1600000000
        self.make_plugin(self.plugin)
        patcher = mock.patch.object(installer, "PLUGIN_ROOT", self.plugin)
        patcher.start()
        self.addCleanup(patcher.stop)
        updater = mock.Mock()
        updater.start_auto_update = mock.Mock()
        self.updater = updater
        patcher = mock.patch.dict(sys.modules, {"plugin_auto_update": updater})
        patcher.start()
        self.addCleanup(patcher.stop)

    def patch_install_stub(self):
        pi = mock.Mock()
        pi.ASAR = self.root / "app.asar"
        return pi

    def make_plugin(self, root, version="1.0.0", prefix="original"):
        for folder, value in ((".zcode-plugin", version), (".claude-plugin", "9.0.0")):
            manifest = root / folder / "plugin.json"
            manifest.parent.mkdir(parents=True, exist_ok=True)
            manifest.write_text(json.dumps({"version": value}), encoding="utf-8")
        for name in installer.RUNTIME_FILES:
            self.write_file(root / name, (prefix + ":" + name).encode("utf-8"))

    def write_file(self, path, data, mtime=None):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        timestamp = self.timestamp if mtime is None else mtime
        os.utime(str(path), (timestamp, timestamp))

    def set_version(self, version):
        (self.plugin / ".zcode-plugin" / "plugin.json").write_text(
            json.dumps({"version": version}), encoding="utf-8")

    def record(self):
        return json.loads((self.runtime / "plugin-runtime.json").read_text(encoding="utf-8"))

    def digest(self, path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def sync(self):
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            result = installer.sync_runtime(self.runtime)
        self.assertEqual(output.getvalue(), "")
        return result, errors.getvalue()

    def run_main(self, command, **environment):
        output, errors = io.StringIO(), io.StringIO()
        env = {"ZUSAGE_PLUGIN_RUNTIME": str(self.runtime)}
        env.update(environment)
        with mock.patch.dict(os.environ, env), mock.patch.object(sys, "argv", ["overlay_install.py", command]), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            installer.main()
        return output.getvalue(), errors.getvalue()

    def test_first_sync_records_preferred_manifest_and_all_hashes(self):
        result, errors = self.sync()
        self.assertEqual(result["changed"], installer.RUNTIME_FILES)
        self.assertEqual(result["skipped"], [])
        self.assertEqual(result["preserved"], [])
        self.assertEqual(result["version"], "1.0.0")
        self.assertEqual(result["plugin_root"], str(self.plugin.resolve()))
        self.assertEqual(self.record(), result)
        self.assertEqual(errors, "")
        for name in installer.RUNTIME_FILES:
            self.assertEqual((self.runtime / name).read_bytes(), (self.plugin / name).read_bytes())
            self.assertEqual(result["hashes"][name], self.digest(self.runtime / name))
            self.assertEqual((self.runtime / name).stat().st_mtime, self.timestamp)
        self.assertIn("plugin-updater.cjs", result["hashes"])

    def test_identical_files_skip_without_replacing_runtime_files(self):
        self.sync()
        destination = self.runtime / "overlay.js"
        os.utime(str(destination), (self.timestamp + 200, self.timestamp + 200))
        with mock.patch.object(installer, "_atomic_write", wraps=installer._atomic_write) as atomic:
            result, _ = self.sync()
        self.assertEqual(result["changed"], [])
        self.assertEqual(result["skipped"], installer.RUNTIME_FILES)
        self.assertEqual(result["preserved"], [])
        self.assertEqual(destination.stat().st_mtime, self.timestamp + 200)
        self.assertEqual(atomic.call_count, 1)
        self.assertEqual(atomic.call_args[0][0], self.runtime / "plugin-runtime.json")

    def test_initial_migration_preserves_newer_and_equal_mtime_edits(self):
        for index, name in enumerate(("overlay.js", "zusage.py")):
            self.write_file(self.runtime / name, b"user hotfix", self.timestamp + index)
        result, errors = self.sync()
        self.assertEqual(result["preserved"], ["overlay.js", "zusage.py"])
        self.assertEqual(self.record()["preserved"], result["preserved"])
        for name in ("overlay.js", "zusage.py"):
            self.assertIn(name, result["skipped"])
            self.assertNotIn(name, result["hashes"])
            self.assertEqual((self.runtime / name).read_bytes(), b"user hotfix")
        self.assertIn("跳过", errors)

    def test_initial_migration_replaces_older_file(self):
        self.write_file(self.runtime / "overlay.js", b"old runtime", self.timestamp - 1)
        result, _ = self.sync()
        self.assertIn("overlay.js", result["changed"])
        self.assertEqual((self.runtime / "overlay.js").read_bytes(), (self.plugin / "overlay.js").read_bytes())

    def test_upgrade_overwrites_managed_file_even_with_newer_runtime_mtime(self):
        self.sync()
        destination = self.runtime / "overlay.js"
        os.utime(str(destination), (self.timestamp + 1000, self.timestamp + 1000))
        self.set_version("1.1.0")
        self.write_file(self.plugin / "overlay.js", b"upgrade", self.timestamp - 1000)
        result, _ = self.sync()
        self.assertEqual(result["changed"], ["overlay.js"])
        self.assertEqual(result["preserved"], [])
        self.assertEqual(destination.read_bytes(), b"upgrade")
        self.assertEqual(self.record()["version"], "1.1.0")
        self.assertEqual(result["hashes"]["overlay.js"], self.digest(destination))

    def test_version_order_is_numeric_and_release_follows_prerelease(self):
        versions = ("1.9.0", "1.10.0", "2.0.0-beta.2", "2.0.0-beta.10", "2.0.0")
        for index, version in enumerate(versions):
            self.set_version(version)
            self.write_file(self.plugin / "overlay.js", version.encode("utf-8"), self.timestamp - index)
            if (self.runtime / "overlay.js").exists():
                os.utime(str(self.runtime / "overlay.js"), (self.timestamp + 1000, self.timestamp + 1000))
            result, _ = self.sync()
            self.assertIn("overlay.js", result["changed"])
            self.assertEqual((self.runtime / "overlay.js").read_text(encoding="utf-8"), version)

    def test_downgrade_cannot_overwrite_or_lower_recorded_version(self):
        self.set_version("2.0.0")
        self.sync()
        original = self.record()
        (self.runtime / "install_monitor.py").unlink()
        old_plugin = self.root / "old-plugin"
        self.make_plugin(old_plugin, "1.0.0", "older")
        for name in installer.RUNTIME_FILES:
            os.utime(str(old_plugin / name), (self.timestamp + 1000, self.timestamp + 1000))
        with mock.patch.object(installer, "PLUGIN_ROOT", old_plugin):
            result, _ = self.sync()
            self.assertEqual(result["version"], "1.0.0")
            self.assertEqual(result["plugin_root"], str(old_plugin.resolve()))
            self.assertEqual(result["changed"], [])
            self.assertEqual(result["skipped"], installer.RUNTIME_FILES)
            self.assertEqual(result["preserved"], [])
            self.assertEqual(self.record()["preserved"], [])
            self.assertEqual(self.record()["version"], "2.0.0")
            self.assertEqual(self.record()["plugin_root"], original["plugin_root"])
            self.assertEqual(self.record()["hashes"], original["hashes"])
            self.assertFalse((self.runtime / "install_monitor.py").exists())
            (old_plugin / ".zcode-plugin" / "plugin.json").write_text(
                json.dumps({"version": "1.5.0"}), encoding="utf-8")
            self.assertEqual(self.sync()[0]["changed"], [])
        self.assertEqual((self.runtime / "overlay.js").read_bytes(), (self.plugin / "overlay.js").read_bytes())

    def test_downgrade_does_not_adopt_manual_content_matching_old_package(self):
        self.set_version("2.0.0")
        self.sync()
        baseline = self.record()["hashes"]["overlay.js"]
        old_plugin = self.root / "old-plugin"
        self.make_plugin(old_plugin, "1.0.0", "older")
        self.write_file(self.runtime / "overlay.js", (old_plugin / "overlay.js").read_bytes(),
                        self.timestamp + 1000)
        with mock.patch.object(installer, "PLUGIN_ROOT", old_plugin):
            result, _ = self.sync()
        self.assertEqual(result["hashes"]["overlay.js"], baseline)
        self.set_version("3.0.0")
        self.write_file(self.plugin / "overlay.js", b"new release")
        result, _ = self.sync()
        self.assertIn("overlay.js", result["skipped"])
        self.assertEqual((self.runtime / "overlay.js").read_bytes(), (old_plugin / "overlay.js").read_bytes())

    def test_upgrade_keeps_newer_manual_edit_and_does_not_adopt_its_hash(self):
        self.sync()
        baseline = self.record()["hashes"]["overlay.js"]
        self.write_file(self.runtime / "overlay.js", b"manual hotfix", self.timestamp + 1000)
        for version in ("1.1.0", "1.2.0"):
            self.set_version(version)
            self.write_file(self.plugin / "overlay.js", version.encode("utf-8"), self.timestamp + 1)
            result, errors = self.sync()
            self.assertIn("overlay.js", result["skipped"])
            self.assertEqual(result["preserved"], ["overlay.js"])
            self.assertEqual((self.runtime / "overlay.js").read_bytes(), b"manual hotfix")
            self.assertEqual(result["hashes"]["overlay.js"], baseline)
            self.assertIn("跳过", errors)

    def test_upgrade_preserves_recorded_manual_edit_with_older_mtime(self):
        self.sync()
        baseline = self.record()["hashes"]["overlay.js"]
        self.write_file(self.runtime / "overlay.js", b"manual hotfix", self.timestamp - 1000)
        self.set_version("1.1.0")
        self.write_file(self.plugin / "overlay.js", b"new release")
        result, errors = self.sync()
        self.assertIn("overlay.js", result["skipped"])
        self.assertEqual(result["preserved"], ["overlay.js"])
        self.assertEqual(self.record()["preserved"], ["overlay.js"])
        self.assertEqual((self.runtime / "overlay.js").read_bytes(), b"manual hotfix")
        self.assertEqual(result["hashes"]["overlay.js"], baseline)
        self.assertIn("hash", errors)

    def test_upgrade_without_hash_baseline_keeps_migration_mtime_strategy(self):
        self.write_file(self.runtime / "overlay.js", b"untracked hotfix", self.timestamp + 1000)
        self.sync()
        self.assertNotIn("overlay.js", self.record()["hashes"])
        self.set_version("1.1.0")
        result, _ = self.sync()
        self.assertEqual(result["preserved"], ["overlay.js"])
        os.utime(str(self.runtime / "overlay.js"), (self.timestamp - 1000, self.timestamp - 1000))
        self.set_version("1.2.0")
        result, _ = self.sync()
        self.assertIn("overlay.js", result["changed"])
        self.assertEqual(result["preserved"], [])

    def test_same_version_preserves_manual_edit_with_older_mtime(self):
        self.sync()
        baseline = self.record()["hashes"]["overlay.js"]
        self.write_file(self.runtime / "overlay.js", b"manual hotfix", self.timestamp - 1000)
        result, _ = self.sync()
        self.assertNotIn("overlay.js", result["changed"])
        self.assertEqual(result["preserved"], ["overlay.js"])
        self.assertEqual(result["hashes"]["overlay.js"], baseline)
        self.assertEqual((self.runtime / "overlay.js").read_bytes(), b"manual hotfix")

    def test_same_version_replaces_managed_file_despite_older_source_mtime(self):
        self.sync()
        destination = self.runtime / "overlay.js"
        before = destination.stat().st_mtime_ns
        self.write_file(self.plugin / "overlay.js", b"same-version package change", self.timestamp - 1)
        result, _ = self.sync()
        self.assertIn("overlay.js", result["changed"])
        self.assertEqual(result["preserved"], [])
        self.assertEqual(destination.read_bytes(), b"same-version package change")
        self.assertNotEqual(destination.stat().st_mtime_ns, before)

    def test_missing_optional_updater_is_skipped(self):
        (self.plugin / "plugin-updater.cjs").unlink()
        result, _ = self.sync()
        self.assertEqual(result["skipped"], ["plugin-updater.cjs"])
        self.assertEqual(result["preserved"], [])
        self.assertNotIn("plugin-updater.cjs", result["hashes"])
        self.assertFalse((self.runtime / "plugin-updater.cjs").exists())

    def test_manifest_falls_back_only_when_preferred_is_missing(self):
        (self.plugin / ".zcode-plugin" / "plugin.json").unlink()
        self.assertEqual(self.sync()[0]["version"], "9.0.0")

    def test_copy_and_record_use_same_directory_atomic_replace(self):
        real_replace = os.replace
        destinations = []

        def inspect_replace(source, destination):
            source, destination = Path(source), Path(destination)
            self.assertEqual(source.parent, destination.parent)
            self.assertNotEqual(source, destination)
            self.assertTrue(source.is_file())
            if destination.name in installer.RUNTIME_FILES:
                self.assertEqual(source.read_bytes(), (self.plugin / destination.name).read_bytes())
            elif destination.name == notice.MARKER:
                marker = json.loads(source.read_text(encoding="utf-8"))
                self.assertEqual(set(marker["hashes"]), {"inject-main.cjs", "plugin-updater.cjs", "plugin-release.cjs", "plugin-storage.cjs"})
                self.assertFalse(any((self.runtime / name).exists() for name in installer.RUNTIME_FILES))
            else:
                self.assertEqual(json.loads(source.read_text(encoding="utf-8"))["version"], "1.0.0")
            destinations.append(destination.name)
            real_replace(source, destination)

        with mock.patch.object(installer.os, "replace", side_effect=inspect_replace):
            self.sync()
        self.assertEqual(destinations, [notice.MARKER] + installer.RUNTIME_FILES + ["plugin-runtime.json"])
        self.assertEqual(sorted(path.name for path in self.runtime.iterdir()),
                         sorted(destinations + ["plugin-runtime.lock", "plugin-restart.lock"]))

    def test_failed_file_replace_preserves_destination_and_cleans_temporary_file(self):
        destination = self.runtime / "inject-main.cjs"
        self.write_file(destination, b"old bytes", self.timestamp - 1)
        with mock.patch.object(installer.os, "replace", side_effect=OSError("replace denied")):
            with self.assertRaisesRegex(OSError, "replace denied"):
                installer.sync_runtime(self.runtime)
        self.assertEqual(destination.read_bytes(), b"old bytes")
        self.assertEqual(sorted(path.name for path in self.runtime.iterdir()),
                         sorted([destination.name, "plugin-runtime.lock", "plugin-restart.lock"]))

    def test_failed_record_replace_does_not_return_success_or_clobber_record(self):
        self.sync()
        record_path = self.runtime / "plugin-runtime.json"
        previous = record_path.read_bytes()
        self.set_version("1.1.0")
        real_replace = os.replace

        def fail_record(source, destination):
            if Path(destination) == record_path:
                raise OSError("record denied")
            real_replace(source, destination)

        with mock.patch.object(installer.os, "replace", side_effect=fail_record):
            with self.assertRaisesRegex(OSError, "record denied"):
                installer.sync_runtime(self.runtime)
        self.assertEqual(record_path.read_bytes(), previous)
        self.assertFalse(list(self.runtime.glob(".plugin-runtime.json.*")))

    def test_sync_does_not_touch_config_optout_or_nudge(self):
        preserved = ("config.json", ".plugin-overlay-optout", ".plugin-overlay-nudge")
        for name in preserved:
            self.write_file(self.runtime / name, b"user settings", self.timestamp + 1000)
            self.write_file(self.plugin / name, b"packaged settings")
        before = {name: ((self.runtime / name).read_bytes(), (self.runtime / name).stat().st_mtime_ns)
                  for name in preserved}
        self.sync()
        self.set_version("2.0.0")
        self.sync()
        for name in preserved:
            self.assertEqual(((self.runtime / name).read_bytes(), (self.runtime / name).stat().st_mtime_ns),
                             before[name])

    def test_sync_cli_emits_only_json_and_never_resolves_asar(self):
        with mock.patch.object(installer, "resolve_paths", side_effect=AssertionError("asar accessed")), \
                mock.patch.object(installer, "load_patch_install", side_effect=AssertionError("patch imported")):
            output, errors = self.run_main("sync", ZUSAGE_PLUGIN_ASAR=str(self.root / "missing.asar"))
        result = json.loads(output)
        self.assertEqual(len(output.splitlines()), 1)
        self.assertEqual(result, self.record())
        self.assertEqual(result["changed"], installer.RUNTIME_FILES)
        self.assertEqual(errors, "")
        self.write_file(self.runtime / "overlay.js", b"manual hotfix", self.timestamp + 1000)
        output, errors = self.run_main("sync")
        result = json.loads(output)
        self.assertIn("overlay.js", result["skipped"])
        self.assertEqual(result["preserved"], ["overlay.js"])
        self.assertEqual(result, self.record())
        self.assertIn("跳过", errors)

    def test_sync_cli_record_failure_exits_nonzero_without_success_json(self):
        self.sync()
        self.set_version("1.1.0")
        output, errors = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, {"ZUSAGE_PLUGIN_RUNTIME": str(self.runtime)}), \
                mock.patch.object(sys, "argv", ["overlay_install.py", "sync"]), \
                mock.patch.object(installer.os, "replace", side_effect=OSError("record denied")), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            with self.assertRaises(SystemExit) as failure:
                installer.main()
        self.assertEqual(failure.exception.code, 1)
        self.assertEqual(output.getvalue(), "")
        self.assertIn("同步失败", errors.getvalue())

    def test_real_sync_subcommand_runs_without_patch_module_or_asar(self):
        (self.plugin / "patch_install.py").unlink()
        environment = os.environ.copy()
        environment.update({"ZUSAGE_PLUGIN_RUNTIME": str(self.runtime),
                            "ZUSAGE_PLUGIN_ASAR": str(self.root / "missing.asar"),
                            "PYTHONDONTWRITEBYTECODE": "1"})
        command = ("import importlib.util; from pathlib import Path; "
                   "s=importlib.util.spec_from_file_location('overlay_install', {!r}); "
                   "m=importlib.util.module_from_spec(s); s.loader.exec_module(m); "
                   "m.PLUGIN_ROOT=Path({!r}); m.main()".format(str(INSTALLER_PATH), str(self.plugin)))
        result = subprocess.run([sys.executable, "-c", command, "sync"], env=environment,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["skipped"], ["patch_install.py"])
        self.assertEqual(len(result.stdout.splitlines()), 1)
        self.assertEqual(result.stderr, "")

    def test_install_still_syncs_installs_and_clears_markers(self):
        self.write_file(self.runtime / ".plugin-overlay-optout", b"1")
        self.write_file(self.runtime / ".plugin-overlay-nudge", b"1")
        patch_install = self.patch_install_stub()
        patch_install.install.return_value = True
        with mock.patch.object(installer, "resolve_paths", return_value=(self.runtime, patch_install)):
            with self.assertRaises(SystemExit) as exit_code:
                self.run_main("install")
        self.assertEqual(exit_code.exception.code, 0)
        patch_install.install.assert_called_once_with()
        self.assertTrue((self.runtime / "plugin-runtime.json").exists())
        self.assertFalse((self.runtime / ".plugin-overlay-optout").exists())
        self.assertFalse((self.runtime / ".plugin-overlay-nudge").exists())

    def test_check_still_checks_asar_without_syncing(self):
        patch_install = self.patch_install_stub()
        patch_install.ASAR = self.root / "app.asar"
        with mock.patch.object(installer, "resolve_paths", return_value=(self.runtime, patch_install)), \
                mock.patch.object(installer, "injected_status", return_value=True), \
                mock.patch.object(installer, "sync_runtime", side_effect=AssertionError("unexpected sync")):
            output, _ = self.run_main("check")
        self.assertIn("injected: 是", output)
        patch_install.check.assert_called_once_with()

    def test_remove_still_sets_optout_marker(self):
        patch_install = self.patch_install_stub()
        patch_install.remove.return_value = True
        with mock.patch.object(installer, "resolve_paths", return_value=(self.runtime, patch_install)):
            with self.assertRaises(SystemExit) as exit_code:
                self.run_main("remove")
        self.assertEqual(exit_code.exception.code, 0)
        patch_install.remove.assert_called_once_with()
        self.assertTrue((self.runtime / ".plugin-overlay-optout").exists())

    def test_hook_update_notification_and_no_stdout(self):
        with mock.patch.object(installer, "resolve_paths", return_value=(self.runtime, self.patch_install_stub())), \
                mock.patch.object(installer, "injected_status", return_value=True):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                result = installer.run_hook()
                unchanged = installer.run_hook()
        self.assertTrue(result["suppressOutput"])
        self.assertIn("inject-main.cjs", result["systemMessage"])
        self.assertIn("plugin-updater.cjs", result["systemMessage"])
        self.assertIn("重启 ZCode", result["systemMessage"])
        self.assertEqual(result["hookSpecificOutput"]["hookEventName"], "SessionStart")
        self.assertTrue((self.runtime / ".plugin-restart-required").is_file())
        self.assertIn("用户可见·重启提醒", unchanged["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(unchanged["systemMessage"], notice.MESSAGE)
        self.assertEqual(output.getvalue(), "")

    def test_hook_updater_only_update_requests_restart(self):
        self.sync()
        self.set_version("1.1.0")
        self.write_file(self.plugin / "plugin-updater.cjs", b"updated updater")
        output = io.StringIO()
        with mock.patch.object(installer, "resolve_paths", return_value=(self.runtime, self.patch_install_stub())), \
                mock.patch.object(installer, "injected_status", return_value=True), \
                contextlib.redirect_stdout(output):
            result = installer.run_hook()
        self.assertEqual(self.record()["changed"], ["plugin-updater.cjs"])
        self.assertIn("plugin-updater.cjs 已更新", result["systemMessage"])
        self.assertIn("重启 ZCode", result["systemMessage"])
        self.assertNotIn("inject-main.cjs", result["systemMessage"])
        self.assertEqual(output.getvalue(), "")

    def test_hook_hot_update_does_not_request_restart(self):
        self.sync()
        (self.runtime / notice.MARKER).unlink()  # 模拟基线已由新 loader 确认
        self.set_version("1.1.0")
        self.write_file(self.plugin / "overlay.js", b"hot update")
        with mock.patch.object(installer, "resolve_paths", return_value=(self.runtime, self.patch_install_stub())), \
                mock.patch.object(installer, "injected_status", return_value=True):
            result = installer.run_hook()
        self.assertEqual(self.record()["changed"], ["overlay.js"])
        self.assertEqual(result, {"suppressOutput": True})

    def test_hook_automatic_install_keeps_protocol_output_clean(self):
        patch_install = self.patch_install_stub()

        def install_overlay():
            print("human-readable install log")
            return True

        patch_install.install.side_effect = install_overlay
        output, errors = io.StringIO(), io.StringIO()
        with mock.patch.object(installer, "resolve_paths", return_value=(self.runtime, patch_install)), \
                mock.patch.object(installer, "injected_status", return_value=False), \
                mock.patch.dict(os.environ, {"ZUSAGE_PLUGIN_AUTO_INSTALL": "1"}), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            result = installer.run_hook()
        self.assertIn("已自动安装", result["systemMessage"])
        self.assertEqual(output.getvalue(), "")
        self.assertIn("human-readable install log", errors.getvalue())

    def test_hook_monitor_flags_are_scoped_and_restored_on_success_or_failure(self):
        for previous in ({}, {"ZUSAGE_NO_MONITOR": "custom", "ZUSAGE_MONITOR_QUIET": "0"}):
            for fail in (False, True):
                with self.subTest(previous=previous, fail=fail):
                    patch_install = self.patch_install_stub()

                    def install_overlay():
                        self.assertEqual(os.environ["ZUSAGE_NO_MONITOR"], "1")
                        self.assertEqual(os.environ["ZUSAGE_MONITOR_QUIET"], "1")
                        if fail:
                            raise RuntimeError("install failed")
                        return True

                    patch_install.install.side_effect = install_overlay
                    environment = dict(previous, ZUSAGE_PLUGIN_AUTO_INSTALL="1")
                    with mock.patch.object(installer, "resolve_paths", return_value=(self.runtime, patch_install)), \
                            mock.patch.object(installer, "injected_status", return_value=False), \
                            mock.patch.dict(os.environ, environment, clear=True), \
                            contextlib.redirect_stderr(io.StringIO()):
                        result = installer.run_hook()
                        for name in ("ZUSAGE_NO_MONITOR", "ZUSAGE_MONITOR_QUIET"):
                            self.assertEqual(os.environ.get(name), previous.get(name))
                    self.assertIn("自检失败" if fail else "已自动安装", result["systemMessage"])

    def test_hook_optout_and_record_failure_are_preserved(self):
        self.write_file(self.runtime / ".plugin-overlay-optout", b"1")
        patch_install = self.patch_install_stub()
        with mock.patch.object(installer, "resolve_paths", return_value=(self.runtime, patch_install)), \
                mock.patch.object(installer, "injected_status", return_value=False):
            self.assertEqual(installer.run_hook(), {"suppressOutput": True})
            with mock.patch.object(installer.os, "replace", side_effect=OSError("record denied")):
                result = installer.run_hook()
        patch_install.install.assert_not_called()
        self.assertIn("自检失败", result["systemMessage"])
        self.assertNotIn("已更新", result["systemMessage"])

    def test_invalid_hash_record_fails_before_any_write_or_false_preservation(self):
        self.sync()
        record_path = self.runtime / "plugin-runtime.json"
        for invalid in ("broken", 1, None, "g" * 64):
            with self.subTest(invalid=invalid):
                record = self.record()
                record["hashes"]["overlay.js"] = invalid
                record_path.write_text(json.dumps(record), encoding="utf-8")
                self.write_file(self.plugin / "overlay.js", b"upgrade", self.timestamp + 1000)
                before = {p.name: p.read_bytes() for p in self.runtime.iterdir() if p.is_file()}
                with mock.patch.object(installer, "_atomic_write") as write:
                    with self.assertRaisesRegex(ValueError, "hash 损坏"):
                        self.sync()
                write.assert_not_called()
                self.assertEqual(before, {p.name: p.read_bytes() for p in self.runtime.iterdir() if p.is_file()})

    def test_same_mtime_changes_stamp_even_when_clock_equals_old_stamp(self):
        self.sync()
        for name in ("overlay.js", "zusage.py"):
            destination = self.runtime / name
            before = destination.stat().st_mtime_ns
            self.write_file(self.plugin / name, b"updated:" + name.encode(), self.timestamp)
            with mock.patch.object(installer.time, "time_ns", return_value=before):
                result, _ = self.sync()
            self.assertIn(name, result["changed"])
            self.assertGreater(destination.stat().st_mtime_ns, before)
            self.assertNotEqual(destination.stat().st_mtime_ns // 1000000, before // 1000000)

    def test_hot_reload_disabled_records_overlay_and_zusage_but_not_tools(self):
        self.sync()
        (self.runtime / notice.MARKER).unlink()
        (self.runtime / "config.json").write_text('{"hot_reload": false}', encoding="utf-8")
        for name in ("overlay.js", "zusage.py", "patch_install.py", "install_monitor.py", "runtime_lock.py"):
            self.write_file(self.plugin / name, b"updated:" + name.encode())
        with mock.patch.object(installer, "resolve_paths", return_value=(self.runtime, self.patch_install_stub())), \
                mock.patch.object(installer, "injected_status", return_value=True):
            output = installer.run_hook()
            unchanged = installer.run_hook()
        marker = json.loads((self.runtime / notice.MARKER).read_text())
        self.assertEqual(set(marker["hashes"]), {"overlay.js", "zusage.py"})
        for name, digest in marker["hashes"].items():
            self.assertEqual(digest, self.digest(self.runtime / name))
        self.assertIn("overlay.js", output["systemMessage"])
        self.assertIn("zusage.py", output["systemMessage"])
        self.assertIn("用户可见·重启提醒", unchanged["hookSpecificOutput"]["additionalContext"])
        self.assertTrue(self.record()["restartRecorded"])

    def test_tool_only_update_with_hot_reload_disabled_does_not_create_marker(self):
        self.sync()
        (self.runtime / notice.MARKER).unlink()
        (self.runtime / "config.json").write_text('{"hot_reload": false}', encoding="utf-8")
        for name in ("patch_install.py", "install_monitor.py", "runtime_lock.py"):
            self.write_file(self.plugin / name, b"tool update")
        result, _ = self.sync()
        self.assertEqual(set(result["changed"]), {"patch_install.py", "install_monitor.py", "runtime_lock.py"})
        self.assertFalse((self.runtime / notice.MARKER).exists())

    def test_marker_failure_blocks_all_runtime_replacements(self):
        self.sync()
        self.write_file(self.plugin / "inject-main.cjs", b"new loader")
        before = {p.name: p.read_bytes() for p in self.runtime.iterdir() if p.is_file()}
        with mock.patch.object(notice, "_record_restart", side_effect=OSError("marker denied")):
            with self.assertRaisesRegex(OSError, "marker denied"):
                self.sync()
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.runtime.iterdir() if p.is_file()})

    def test_loader_ack_during_sync_is_not_overwritten_by_hook_postwrite(self):
        import runtime_lock
        self.sync()
        (self.runtime / notice.MARKER).unlink()
        self.write_file(self.plugin / "inject-main.cjs", b"new loader")
        real_write = installer._atomic_write

        def acknowledge_after_write(destination, data, source=None):
            real_write(destination, data, source)
            if destination.name == "inject-main.cjs":
                runtime_lock.ack_restart(self.runtime, {"inject-main.cjs": self.digest(destination)})

        with mock.patch.object(installer, "_atomic_write", side_effect=acknowledge_after_write), \
                mock.patch.object(installer, "resolve_paths", return_value=(self.runtime, self.patch_install_stub())), \
                mock.patch.object(installer, "injected_status", return_value=True):
            self.assertEqual(installer.run_hook(), {"suppressOutput": True})
        self.assertFalse((self.runtime / notice.MARKER).exists())

    def test_partial_file_failure_same_package_retry_commits_without_false_hotfix(self):
        self.sync()
        self.set_version("1.1.0")
        self.write_file(self.plugin / "overlay.js", b"updated overlay")
        self.write_file(self.plugin / "zusage.py", b"updated python")
        real_write = installer._atomic_write

        def fail_second(destination, data, source=None):
            if destination.name == "zusage.py":
                raise OSError("python denied")
            real_write(destination, data, source)

        with mock.patch.object(installer, "_atomic_write", side_effect=fail_second):
            with self.assertRaisesRegex(OSError, "python denied"):
                self.sync()
        self.assertEqual(self.record()["version"], "1.0.0")
        result, _ = self.sync()
        self.assertEqual(result["preserved"], [])
        self.assertEqual(result["changed"], ["zusage.py"])
        self.assertEqual(self.record()["version"], "1.1.0")
        for name in ("overlay.js", "zusage.py"):
            self.assertEqual(self.record()["hashes"][name], self.digest(self.runtime / name))

    def test_record_failure_same_package_retry_commits_deployed_hashes(self):
        self.sync()
        self.set_version("1.1.0")
        self.write_file(self.plugin / "overlay.js", b"updated overlay")
        real_write = installer._atomic_write

        def fail_record(destination, data, source=None):
            if destination.name == "plugin-runtime.json":
                raise OSError("record denied")
            real_write(destination, data, source)

        with mock.patch.object(installer, "_atomic_write", side_effect=fail_record):
            with self.assertRaisesRegex(OSError, "record denied"):
                self.sync()
        result, _ = self.sync()
        self.assertEqual(result["changed"], [])
        self.assertEqual(result["preserved"], [])
        self.assertEqual(self.record()["version"], "1.1.0")
        self.assertEqual(result["hashes"]["overlay.js"], self.digest(self.runtime / "overlay.js"))

    def test_auto_install_config_requires_literal_true_and_environment_remains_explicit(self):
        self.runtime.mkdir()
        with mock.patch.dict(os.environ, {}, clear=True):
            for value in (False, "false", "true", 1, 0, None, [], {}):
                with self.subTest(value=value):
                    (self.runtime / "config.json").write_text(
                        json.dumps({"plugin_auto_install_overlay": value}), encoding="utf-8")
                    self.assertFalse(installer.auto_enabled(self.runtime))
            (self.runtime / "config.json").write_text('{"plugin_auto_install_overlay": true}', encoding="utf-8")
            self.assertTrue(installer.auto_enabled(self.runtime))
            for value, expected in (("1", True), ("true", True), ("YES", True), ("on", True),
                                    ("false", False), ("0", False), ("", False)):
                os.environ["ZUSAGE_PLUGIN_AUTO_INSTALL"] = value
                self.assertIs(installer.auto_enabled(self.runtime), expected)

    def test_explicit_install_with_existing_injection_retains_target_hash_marker(self):
        self.sync()
        (self.runtime / notice.MARKER).unlink()
        self.write_file(self.plugin / "plugin-updater.cjs", b"updated updater")
        pi = self.patch_install_stub()
        pi.install.return_value = True
        output = io.StringIO()
        with mock.patch.object(installer, "resolve_paths", return_value=(self.runtime, pi)), \
                mock.patch.object(installer, "injected_status", return_value=True), \
                mock.patch.object(sys, "argv", ["overlay_install.py", "install"]), \
                contextlib.redirect_stdout(output):
            with self.assertRaises(SystemExit) as exited:
                installer.main()
        self.assertEqual(exited.exception.code, 0)
        self.assertIn(notice.MESSAGE, output.getvalue())
        marker = json.loads((self.runtime / notice.MARKER).read_text())
        self.assertEqual(marker["hashes"], {"plugin-updater.cjs": self.digest(self.runtime / "plugin-updater.cjs")})

    def test_actual_process_transaction_blocks_old_hook_until_new_baseline_commits(self):
        self.sync()
        newer, older = self.root / "newer", self.root / "older"
        self.make_plugin(newer, "3.0.0", "newest")
        self.make_plugin(older, "2.0.0", "outdated")
        entered, attempted, baseline_read, release = [self.root / name for name in
                                                     ("entered", "attempted", "baseline-read", "release")]
        script = r'''
import contextlib, importlib.util, json, sys, time
from pathlib import Path
spec = importlib.util.spec_from_file_location("process_installer", sys.argv[1])
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
m.PLUGIN_ROOT = Path(sys.argv[2]); runtime = Path(sys.argv[3]); mode = sys.argv[4]
entered, attempted, baseline_read, release = map(Path, sys.argv[5:9])
read = Path.read_text
if mode == "new":
    def gated_read(path, *args, **kwargs):
        if path == runtime / "plugin-runtime.json":
            entered.write_text("1")
            deadline = time.monotonic() + 8
            while not release.exists():
                if time.monotonic() > deadline: raise TimeoutError("test release")
                time.sleep(.01)
        return read(path, *args, **kwargs)
    Path.read_text = gated_read
else:
    lock = m.file_lock
    @contextlib.contextmanager
    def attempted_lock(path, timeout=10.0):
        attempted.write_text("1")
        with lock(path, timeout): yield
    m.file_lock = attempted_lock
    def observed_read(path, *args, **kwargs):
        if path == runtime / "plugin-runtime.json": baseline_read.write_text("1")
        return read(path, *args, **kwargs)
    Path.read_text = observed_read
print(json.dumps(m.sync_runtime(runtime)), flush=True)
'''
        common = [sys.executable, "-c", script, str(INSTALLER_PATH)]
        markers = list(map(str, (entered, attempted, baseline_read, release)))
        environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        new = subprocess.Popen(common + [str(newer), str(self.runtime), "new"] + markers,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=environment)
        old = None
        try:
            deadline = time.monotonic() + 5
            while not entered.exists() and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertTrue(entered.exists(), "new process did not reach baseline read")
            old = subprocess.Popen(common + [str(older), str(self.runtime), "old"] + markers,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=environment)
            while not attempted.exists() and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertTrue(attempted.exists())
            time.sleep(.15)
            self.assertFalse(baseline_read.exists(), "old hook read a stale baseline outside transaction lock")
            self.assertIsNone(old.poll())
            release.write_text("1")
            new_out, new_err = new.communicate(timeout=10)
            old_out, old_err = old.communicate(timeout=10)
            self.assertEqual(new.returncode, 0, new_err)
            self.assertEqual(old.returncode, 0, old_err)
            self.assertEqual(json.loads(new_out)["changed"], installer.RUNTIME_FILES)
            self.assertEqual(json.loads(old_out)["changed"], [])
            self.assertEqual(self.record()["version"], "3.0.0")
            for name in installer.RUNTIME_FILES:
                self.assertEqual((self.runtime / name).read_bytes(), (newer / name).read_bytes())
            self.assertTrue((self.runtime / "plugin-runtime.lock").is_file())
        finally:
            release.write_text("1")
            for process in (new, old):
                if process is not None:
                    if process.poll() is None:
                        process.kill()
                    process.communicate(timeout=5)

    @unittest.skipUnless(shutil.which("node"), "Node is needed for actual loader integration")
    def test_same_source_mtime_sync_reloads_actual_loader_overlay_and_python(self):
        self.write_file(self.plugin / "overlay.js", b"globalThis.overlayRevision = 1;")
        self.write_file(self.plugin / "zusage.py", b"# revision 1\n")
        self.sync()
        script = r'''
const fs = require("fs"), path = require("path"), vm = require("vm"), assert = require("assert/strict");
const runtime = process.argv[1], source = fs.readFileSync(process.argv[2], "utf8");
const intervals = [], windows = [], injected = []; let killed = 0;
const sandbox = { __dirname: runtime, console: { error() {} },
  process: { uptime: () => 1, platform: process.platform, resourcesPath: runtime, execPath: process.execPath },
  setInterval: (fn, ms) => intervals.push({ fn, ms }), setTimeout: () => 1, clearTimeout() {},
  require(id) {
    if (id === "fs") return Object.assign({}, fs, { watch: () => ({ on() {} }) });
    if (["path", "url", "crypto"].includes(id)) return require(id);
    if (id === "child_process") return { execFile: (a,b,c,cb) => cb(null), spawn() { throw Error("unexpected spawn"); } };
    if (id === "electron") return { app: { getPath: () => runtime, on() {} }, webContents: { getAllWebContents: () => windows }, ipcMain: { on() {} } };
    if (id === "./plugin-updater.cjs") return { createUpdater: () => ({ status: () => ({}), tick: async () => {}, request: async () => {} }) };
    throw Error(id);
  }, onKill: () => killed++ };
vm.createContext(sandbox); vm.runInContext(source, sandbox);
const hot = intervals.find(i => i.ms === 2000); assert.ok(hot); hot.fn();
windows.push({ getType: () => "window", isDestroyed: () => false,
  executeJavaScript: async src => { injected.push(src); }, on() {}, isLoading: () => false });
vm.runInContext("pyRes = {proc: {kill: onKill}}", sandbox);
process.stdout.write("ready\n");
process.stdin.once("data", async () => {
  try {
    hot.fn(); await new Promise(resolve => setImmediate(resolve));
    assert.ok(injected.includes("globalThis.overlayRevision = 2;"), "actual loader did not re-inject changed overlay");
    assert.equal(killed, 1, "actual loader did not restart changed resident Python");
    console.log(JSON.stringify({ overlayReloaded: true, pythonRestarted: true }));
  } catch (e) { console.error(e); process.exitCode = 1; }
  process.stdin.pause();
});
'''
        loader = INSTALLER_PATH.parents[2] / "inject-main.cjs"
        process = subprocess.Popen(["node", "-e", script, str(self.runtime), str(loader)],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(process.stdout.readline().strip(), "ready")
            before = {name: (self.runtime / name).stat().st_mtime_ns for name in ("overlay.js", "zusage.py")}
            self.write_file(self.plugin / "overlay.js", b"globalThis.overlayRevision = 2;")
            self.write_file(self.plugin / "zusage.py", b"# revision 2\n")
            self.assertEqual((self.plugin / "overlay.js").stat().st_mtime_ns, before["overlay.js"])
            result, _ = self.sync()
            self.assertEqual(result["changed"], ["overlay.js", "zusage.py"])
            output, errors = process.communicate("updated\n", timeout=10)
            self.assertEqual(process.returncode, 0, errors)
            self.assertEqual(json.loads(output), {"overlayReloaded": True, "pythonRestarted": True})
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)

    def test_installer_syntax_is_python38_compatible(self):
        ast.parse(INSTALLER_PATH.read_text(encoding="utf-8"), feature_version=(3, 8))


class SessionStartDiagnosticTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.runtime = self.root / "runtime"
        self.runtime.mkdir()
        self.home = self.root / "home"
        self.home.mkdir()
        spec = importlib.util.spec_from_file_location(
            "plugin_session_start_fixture", INSTALLER_PATH.parent / "hooks" / "session-start.py")
        self.hook = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.hook)
        self.hook.PLUGIN_ROOT = self.root / "plugin"
        self.result = {"suppressOutput": True, "systemMessage": "隔离自检结果"}
        self.overlay = mock.Mock(spec=["run_hook", "_atomic_write"])
        self.overlay.run_hook.return_value = self.result
        self.overlay._atomic_write = mock.Mock(wraps=installer._atomic_write)
        self.rate = mock.Mock(spec=["write_session_state"])

    def run_hook(self, raw):
        output, errors = io.StringIO(), io.StringIO()
        environment = {"HOME": str(self.home), "USERPROFILE": str(self.home),
                       "ZUSAGE_PLUGIN_RUNTIME": str(self.runtime),
                       "ZCODE_SESSION_ID": "test-session", "CLAUDE_SESSION_ID": "fallback-session"}
        with mock.patch.dict(os.environ, environment, clear=True), \
                mock.patch.dict(sys.modules, {"overlay_install": self.overlay, "token_rate": self.rate}), \
                mock.patch.object(sys, "path", list(sys.path)), \
                mock.patch.object(sys, "stdin", io.StringIO(raw)), \
                mock.patch.object(self.hook.time, "time", return_value=1600000000.5), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            self.hook.main()
        self.assertEqual(json.loads(output.getvalue()), self.result)
        self.assertEqual(output.getvalue(), json.dumps(self.result, ensure_ascii=False) + "\n")
        self.overlay.run_hook.assert_called_once_with()
        self.assertEqual(self.rate.mock_calls,
                         [mock.call.write_session_state("test-session", "session-start")])
        self.assertEqual(len(self.overlay._atomic_write.call_args_list), 1)
        destination, data = self.overlay._atomic_write.call_args[0]
        self.assertEqual(destination, self.runtime / "plugin-session-start.json")
        return json.loads(data), errors.getvalue()

    def test_normal_source_records_all_fields_without_changing_hook_result_or_rate_call(self):
        record, errors = self.run_hook(json.dumps({"source": "resume", "sessionId": "stdin-session"}))
        self.assertEqual(record, {"time": 1600000000.5, "pluginRoot": str(self.hook.PLUGIN_ROOT),
                                  "source": "resume", "sessionId": "test-session", "result": self.result})
        self.assertEqual(json.loads((self.runtime / "plugin-session-start.json").read_text(encoding="utf-8")),
                         record)
        self.assertEqual(errors, "")
        self.assertEqual(list(self.runtime.iterdir()), [self.runtime / "plugin-session-start.json"])
        self.assertEqual(list(self.home.iterdir()), [])

    def test_invalid_stdin_records_empty_source_and_keeps_stdout_json(self):
        record, errors = self.run_hook("{invalid json")
        self.assertEqual(record["source"], "")
        self.assertEqual(record["sessionId"], "test-session")
        self.assertEqual(json.loads((self.runtime / "plugin-session-start.json").read_text(encoding="utf-8")),
                         record)
        self.assertEqual(errors, "")

    def test_diagnostic_write_failure_only_uses_stderr_and_does_not_block(self):
        self.overlay._atomic_write.side_effect = OSError("diagnostic denied")
        record, errors = self.run_hook('{"source": "startup"}')
        self.assertEqual(record["source"], "startup")
        self.assertIn("会话启动诊断写入失败", errors)
        self.assertIn("diagnostic denied", errors)
        self.assertEqual(list(self.runtime.iterdir()), [])
        self.assertEqual(list(self.home.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
