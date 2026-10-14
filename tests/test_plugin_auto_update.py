# -*- coding: utf-8 -*-
"""自动更新 bootstrap 的隔离测试：临时路径、mock 时钟和 Popen，无真实进程。"""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


HELPER_PATH = (Path(__file__).resolve().parents[1] / "plugins" /
               "zcode-token-usage-statusbar" / "plugin_auto_update.py")
spec = importlib.util.spec_from_file_location("plugin_auto_update", str(HELPER_PATH))
updater = importlib.util.module_from_spec(spec)
spec.loader.exec_module(updater)


class PluginAutoUpdateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.runtime = self.root / "runtime with spaces"
        self.asar = self.root / "ZCode with spaces" / "resources" / "app.asar"
        self.bundle = self.asar.parent / "glm" / "zcode.cjs"
        self.executable = self.asar.parent.parent / "ZCode"
        self.loader = self.runtime / "plugin-updater.cjs"
        for path in (self.asar, self.bundle, self.executable, self.loader):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"test fixture")
        self.executable.chmod(0o755)
        self.pi = SimpleNamespace(ASAR=self.asar, ZCODE_EXE=self.executable,
                                  zcode_exe_for=mock.Mock(return_value=self.executable))
        self.now = 1800000000
        self.write_config({"plugin_auto_update": True})
        for target, value in (("subprocess.Popen", mock.DEFAULT),
                              ("time.time", mock.DEFAULT), ("sys.platform", "linux")):
            # 模块用 spec 加载，不依赖 sys.path 或真实插件缓存。
            owner, attribute = target.split(".")
            patcher = mock.patch.object(getattr(updater, owner), attribute, value)
            patched = patcher.start()
            self.addCleanup(patcher.stop)
            if target == "subprocess.Popen":
                self.popen = patched
            elif target == "time.time":
                patched.return_value = self.now

    def write_config(self, config):
        (self.runtime / "config.json").write_text(json.dumps(config), encoding="utf-8")

    def write_state(self, state):
        (self.runtime / "plugin-update.json").write_text(json.dumps(state), encoding="utf-8")

    def launch(self):
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            result = updater.start_auto_update(self.runtime, self.pi)
        self.assertEqual(output.getvalue(), "")
        return result, errors.getvalue()

    def assert_silent_skip(self):
        self.assertEqual(self.launch(), (False, ""))
        self.popen.assert_not_called()

    def test_true_starts_detached_once_with_exact_argv_and_no_wait(self):
        self.assertEqual(self.launch(), (True, ""))
        self.popen.assert_called_once()
        args, kwargs = self.popen.call_args
        self.assertEqual(args, ([str(self.executable), str(self.loader), "--auto",
                                 "--resources", str(self.asar.parent), "--runtime",
                                 str(self.runtime)],))
        for stream in ("stdin", "stdout", "stderr"):
            self.assertEqual(kwargs[stream], updater.subprocess.DEVNULL)
        self.assertTrue(kwargs["start_new_session"])
        self.assertNotIn("creationflags", kwargs)
        self.assertFalse(kwargs["shell"])
        self.assertEqual(kwargs["cwd"], str(Path.home()))
        self.pi.zcode_exe_for.assert_not_called()
        self.popen.return_value.wait.assert_not_called()
        self.popen.return_value.communicate.assert_not_called()

    def test_explicit_non_true_values_disable_updates(self):
        for value in (False, None, 0, 1, "true", "false", [], {}):
            with self.subTest(value=value):
                self.write_config({"plugin_auto_update": value})
                self.assert_silent_skip()

    def test_missing_auto_setting_or_config_defaults_to_on(self):
        for config in ({}, {"lang": "en", "plugin_auto_install_overlay": False}, None):
            with self.subTest(config=config):
                self.popen.reset_mock()
                path = self.runtime / "config.json"
                if config is None:
                    path.unlink()
                else:
                    self.write_config(config)
                before = path.read_bytes() if path.exists() else None
                self.assertEqual(self.launch(), (True, ""))
                self.popen.assert_called_once()
                self.assertEqual(path.read_bytes() if path.exists() else None, before)
                self.assertEqual(self.asar.read_bytes(), b"test fixture")

    def test_corrupt_or_non_object_config_is_noop(self):
        config = self.runtime / "config.json"
        for data in (b"{", b"\xff", b"[]", b"null", b"true", b'"text"'):
            with self.subTest(data=data):
                config.write_bytes(data)
                self.assert_silent_skip()
                self.assertEqual(config.read_bytes(), data)
        config.unlink()
        config.mkdir()
        self.assert_silent_skip()

    def test_auto_install_environment_and_config_cannot_override_disabled_updates(self):
        with mock.patch.dict(os.environ, {"ZUSAGE_PLUGIN_AUTO_INSTALL": "1"}):
            self.write_config({"plugin_auto_update": False, "plugin_auto_install_overlay": True})
            self.assert_silent_skip()

    def test_update_does_not_modify_config_or_optout_or_inject(self):
        self.write_config({"plugin_auto_update": True, "plugin_auto_install_overlay": False})
        marker = self.runtime / ".plugin-overlay-optout"
        marker.write_bytes(b"1")
        config = (self.runtime / "config.json").read_bytes()
        asar = self.asar.read_bytes()
        self.assertEqual(self.launch(), (True, ""))
        self.assertEqual(marker.read_bytes(), b"1")
        self.assertEqual((self.runtime / "config.json").read_bytes(), config)
        self.assertEqual(self.asar.read_bytes(), asar)
        self.assertFalse((self.runtime / ".plugin-overlay-nudge").exists())
        self.assertFalse((self.runtime / "plugin-update.json").exists())

    def test_environment_is_inherited_and_python_comes_from_hook(self):
        self.write_config({"plugin_auto_update": True, "python_path": "/fake/config/python"})
        inherited = {"ZUSAGE_TEST_ENV": "keep me", "ELECTRON_RUN_AS_NODE": "0",
                     "ZUSAGE_PLUGIN_PYTHON": "/fake/env/python"}
        with mock.patch.dict(os.environ, inherited), mock.patch.object(sys, "executable", "/real/python"):
            before = os.environ.copy()
            self.assertEqual(self.launch(), (True, ""))
            env = self.popen.call_args.kwargs["env"]
            self.assertEqual(env, dict(before, ELECTRON_RUN_AS_NODE="1", ZUSAGE_PLUGIN_PYTHON="/real/python"))
            self.assertEqual(os.environ.copy(), before)

    def test_recent_last_attempt_skips_without_touching_state(self):
        self.write_state({"lastAttempt": self.now * 1000 - 1000})
        state = (self.runtime / "plugin-update.json").read_bytes()
        self.assert_silent_skip()
        self.assertEqual((self.runtime / "plugin-update.json").read_bytes(), state)

    def test_interval_boundary_uses_milliseconds_and_last_attempt(self):
        for elapsed, launches in ((updater.INTERVAL_MS - 1, False),
                                  (updater.INTERVAL_MS, True), (updater.INTERVAL_MS + 1, True)):
            with self.subTest(elapsed=elapsed):
                self.popen.reset_mock()
                self.write_state({"lastAttempt": self.now * 1000 - elapsed,
                                  "checkedAt": self.now * 1000})
                self.assertEqual(self.launch(), (launches, ""))
                self.assertEqual(self.popen.call_count, int(launches))

    def test_pending_sync_ignores_interval_only_for_literal_true(self):
        for pending, launches in ((True, True), (False, False), (1, False), ("true", False)):
            with self.subTest(pending=pending):
                self.popen.reset_mock()
                self.write_state({"lastAttempt": self.now * 1000, "pendingSync": pending})
                self.assertEqual(self.launch(), (launches, ""))
                self.assertEqual(self.popen.call_count, int(launches))

    def test_pending_sync_cannot_override_disabled_config(self):
        self.write_config({"plugin_auto_update": False})
        self.write_state({"lastAttempt": self.now * 1000, "pendingSync": True})
        self.assert_silent_skip()

    def test_invalid_update_state_allows_attempt(self):
        for state in ({}, [], None, True, {"lastAttempt": "recent"}, {"lastAttempt": True},
                      {"lastAttempt": 0}, {"lastAttempt": -1}, {"lastAttempt": []},
                      {"lastAttempt": float("nan")}, {"lastAttempt": float("inf")},
                      {"lastAttempt": self.now * 1000 + updater.INTERVAL_MS}):
            with self.subTest(state=state):
                self.popen.reset_mock()
                self.write_state(state)
                self.assertEqual(self.launch(), (True, ""))
                self.popen.assert_called_once()
        for data in (b"{", b"\xff"):
            with self.subTest(data=data):
                self.popen.reset_mock()
                (self.runtime / "plugin-update.json").write_bytes(data)
                self.assertEqual(self.launch(), (True, ""))
                self.popen.assert_called_once()
        (self.runtime / "plugin-update.json").unlink()
        (self.runtime / "plugin-update.json").mkdir()
        self.popen.reset_mock()
        self.assertEqual(self.launch(), (True, ""))
        self.popen.assert_called_once()

    def test_missing_or_unreadable_asar_is_silent(self):
        self.asar.unlink()
        self.assert_silent_skip()
        self.asar.mkdir()
        self.assert_silent_skip()
        self.asar.rmdir()
        self.asar.write_bytes(b"fixture")
        original = Path.open

        def unreadable(path, *args, **kwargs):
            if path == self.asar:
                raise PermissionError("asar access denied")
            return original(path, *args, **kwargs)

        with mock.patch.object(Path, "open", unreadable):
            self.assert_silent_skip()
        self.pi.zcode_exe_for.assert_not_called()

    def test_missing_bundle_warns_only_for_enabled_local_asar(self):
        self.bundle.unlink()
        result, errors = self.launch()
        self.assertFalse(result)
        self.assertIn("bundle", errors)
        self.assertIn(str(self.bundle), errors)
        self.popen.assert_not_called()
        self.write_config({"plugin_auto_update": False})
        self.assert_silent_skip()
        self.write_config({"plugin_auto_update": True})
        self.asar.unlink()
        self.assert_silent_skip()

    def test_missing_loader_warns_and_does_not_spawn(self):
        self.loader.unlink()
        result, errors = self.launch()
        self.assertFalse(result)
        self.assertIn(str(self.loader), errors)
        self.popen.assert_not_called()

    def test_falls_back_to_executable_inferred_from_asar(self):
        for configured in (None, self.root / "missing executable"):
            with self.subTest(configured=configured):
                self.popen.reset_mock()
                self.pi.zcode_exe_for.reset_mock()
                self.pi.ZCODE_EXE = configured
                self.assertEqual(self.launch(), (True, ""))
                self.pi.zcode_exe_for.assert_called_once_with(self.asar)
                self.assertEqual(self.popen.call_args.args[0][0], str(self.executable))

    def test_invalid_executable_warns_and_does_not_spawn(self):
        self.pi.ZCODE_EXE = None
        for inferred in (None, self.root / "missing executable", self.executable.parent):
            with self.subTest(inferred=inferred):
                self.pi.zcode_exe_for.return_value = inferred
                result, errors = self.launch()
                self.assertFalse(result)
                self.assertIn("可执行文件", errors)
                self.popen.assert_not_called()

    def test_posix_non_executable_file_is_rejected(self):
        self.executable.chmod(0o644)
        result, errors = self.launch()
        self.assertFalse(result)
        self.assertIn("可执行文件", errors)
        self.popen.assert_not_called()

    def test_windows_detach_flags_and_no_console(self):
        self.executable.chmod(0o644)
        flags = {"DETACHED_PROCESS": 0x8, "CREATE_NEW_PROCESS_GROUP": 0x200,
                 "CREATE_NO_WINDOW": 0x8000000}
        with mock.patch.object(sys, "platform", "win32"), \
                mock.patch.multiple(updater.subprocess, create=True, **flags):
            self.assertEqual(self.launch(), (True, ""))
        kwargs = self.popen.call_args.kwargs
        self.assertEqual(kwargs["creationflags"], 0x8 | 0x200 | 0x8000000)
        self.assertNotIn("start_new_session", kwargs)
        self.assertFalse(kwargs["shell"])
        for stream in ("stdin", "stdout", "stderr"):
            self.assertEqual(kwargs[stream], updater.subprocess.DEVNULL)

    def test_launch_failure_warns_on_stderr_and_never_raises(self):
        for error in (OSError("permission denied"), RuntimeError("launch failed")):
            with self.subTest(error=error):
                self.popen.reset_mock()
                self.popen.side_effect = error
                result, errors = self.launch()
                self.assertFalse(result)
                self.assertIn("自动更新启动失败", errors)
                self.assertIn(str(error), errors)
                self.popen.assert_called_once()

    def test_executable_inference_failure_does_not_escape_hook(self):
        self.pi.ZCODE_EXE = None
        self.pi.zcode_exe_for.side_effect = RuntimeError("cannot infer executable")
        result, errors = self.launch()
        self.assertFalse(result)
        self.assertIn("cannot infer executable", errors)
        self.popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
