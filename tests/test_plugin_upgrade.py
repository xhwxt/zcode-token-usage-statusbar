# -*- coding: utf-8 -*-
"""使用临时微型 asar 验证目标迁移、客户端升级覆盖及配置安全。"""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import struct
import sys
import tempfile
import types
import unittest
from unittest import mock


PLUGIN_ROOT = (Path(__file__).resolve().parents[1] / "plugins" /
               "zcode-token-usage-statusbar")


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PluginUpgradeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.runtime = self.root / "runtime"
        self.plugin = self.root / "plugin"
        self.asar = self.root / "custom-location" / "resources" / "app.asar"
        self.installer = load_module("overlay_upgrade_test", PLUGIN_ROOT / "overlay_install.py")
        self.real_candidates = self.installer.asar_candidates
        self.pi = load_module("patch_upgrade_test", PLUGIN_ROOT / "patch_install.py")
        self.pi._LANG = "zh"  # 不读取真实用户的语言配置。
        self.body = b"export const clientVersion = '1.0.0';\n"
        self.write_asar(self.asar)
        manifest = self.plugin / ".zcode-plugin" / "plugin.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text('{"version":"1.0.0"}', encoding="utf-8")
        for name in self.installer.RUNTIME_FILES:
            (self.plugin / name).write_bytes((PLUGIN_ROOT / name).read_bytes())
        self.updater = types.SimpleNamespace(start_auto_update=mock.Mock())
        for patcher in (
                mock.patch.object(self.installer, "PLUGIN_ROOT", self.plugin),
                mock.patch.object(self.installer, "load_patch_install", return_value=self.pi),
                mock.patch.object(self.installer, "asar_candidates", return_value=[]),
                mock.patch.object(self.pi, "syntax_check", return_value=True),
                mock.patch.object(self.pi, "launch_monitor"),
                mock.patch.object(self.pi, "client_running", return_value=False),
                mock.patch.dict(sys.modules, {"plugin_auto_update": self.updater,
                    "restart_notice": load_module("upgrade_restart_notice", PLUGIN_ROOT / "restart_notice.py")}),
                mock.patch.dict(os.environ, {"ZUSAGE_PLUGIN_RUNTIME": str(self.runtime)}, clear=True)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def write_asar(self, path, entry=None, name="zcode-desktop", version="1.0.0", unpacked=False):
        files = {
            "package.json": json.dumps({"name": name, "version": version}).encode("utf-8"),
            "out/main/index.js": self.body if entry is None else entry,
            "assets/unchanged.txt": b"official resource",
        }
        header, payload = {"files": {}}, bytearray()
        for name, data in files.items():
            parent = header
            parts = name.split("/")
            for part in parts[:-1]:
                parent = parent["files"].setdefault(part, {"files": {}})
            node = {"offset": str(len(payload)), "size": len(data)}
            if name == "package.json" and unpacked:
                node["unpacked"] = True
            parent["files"][parts[-1]] = node
            payload.extend(data)
        encoded = json.dumps(header, separators=(",", ":")).encode("utf-8")
        aligned = (len(encoded) + 7) // 4 * 4
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(struct.pack("<4I", 4, aligned + 4, aligned, len(encoded)) +
                         encoded + b"\0" * (aligned - len(encoded) - 4) + payload)

    def write_config(self, config):
        self.runtime.mkdir(parents=True, exist_ok=True)
        (self.runtime / "config.json").write_text(json.dumps(config), encoding="utf-8")

    def config(self):
        return json.loads((self.runtime / "config.json").read_text(encoding="utf-8"))

    def main(self, command):
        output, errors = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "argv", ["overlay_install.py", command]), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            with self.assertRaises(SystemExit) as exit_code:
                self.installer.main()
        return exit_code.exception.code, output.getvalue(), errors.getvalue()

    def hook(self):
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            result = self.installer.run_hook()
        self.assertEqual(output.getvalue(), "")
        return result, errors.getvalue()

    def test_platform_candidates_include_common_and_nonstandard_windows_programs(self):
        local = self.root / "LocalAppData"
        custom = local / "Programs" / "custom-channel" / "resources" / "app.asar"
        self.write_asar(custom)
        os.environ.update({"LOCALAPPDATA": str(local), "ProgramFiles": str(self.root / "ProgramFiles")})
        with mock.patch.object(self.installer.sys, "platform", "win32"):
            candidates = self.real_candidates()
            self.assertIn(local / "Programs/ZCode/resources/app.asar", candidates)
            self.assertIn(local / "ZCode/resources/app.asar", candidates)
            self.assertIn(self.root / "ProgramFiles/ZCode/resources/app.asar", candidates)
            self.assertIn(custom, candidates)
        with mock.patch.object(self.installer, "asar_candidates", return_value=[custom]):
            self.assertEqual(self.installer.resolve_paths()[1].ASAR, custom)
        with mock.patch.object(self.installer.sys, "platform", "darwin"), \
                mock.patch.object(self.installer.Path, "home", return_value=self.root):
            self.assertIn(self.root / "Applications/ZCode.app/Contents/Resources/app.asar",
                          self.real_candidates())
        with mock.patch.object(self.installer.sys, "platform", "linux"):
            self.assertIn(Path("/opt/ZCode/resources/app.asar"), self.real_candidates())
            self.assertIn(Path("/usr/lib/zcode/resources/app.asar"), self.real_candidates())

    def test_production_precedence_environment_config_candidates(self):
        configured = self.root / "configured" / "app.asar"
        discovered = self.root / "discovered" / "app.asar"
        self.write_asar(configured)
        self.write_asar(discovered)
        self.write_config({"asar_path": str(configured)})
        os.environ["ZCODE_ASAR"] = str(self.asar)
        with mock.patch.object(self.installer, "asar_candidates", return_value=[discovered]):
            self.assertEqual(self.installer.resolve_paths()[1].ASAR, self.asar)
            del os.environ["ZCODE_ASAR"]
            self.assertEqual(self.installer.resolve_paths()[1].ASAR, configured)
            configured.unlink()
            self.assertEqual(self.installer.resolve_paths()[1].ASAR, discovered)

    def test_test_override_has_explicit_priority_and_skips_package_validation(self):
        foreign = self.root / "foreign.asar"
        self.write_asar(foreign, name="another-electron-client")
        self.write_config({"asar_path": str(self.asar)})
        os.environ.update({"ZUSAGE_PLUGIN_ASAR": str(foreign), "ZCODE_ASAR": str(self.asar)})
        with mock.patch.object(self.installer, "is_zcode_asar", side_effect=AssertionError("validated test")):
            self.assertEqual(self.installer.resolve_paths()[1].ASAR, foreign)

    def test_foreign_asar_is_rejected_for_every_production_source(self):
        foreign = self.root / "ZCode" / "resources" / "app.asar"
        self.write_asar(foreign, name="another-electron-client")
        # 文件名甚至可执行文件名均不是身份依据。
        (foreign.parent.parent / "ZCode.exe").write_bytes(b"not ZCode")
        self.write_config({"asar_path": str(foreign)})
        os.environ["ZCODE_ASAR"] = str(foreign)
        before = foreign.read_bytes()
        with mock.patch.object(self.installer, "asar_candidates", return_value=[foreign]):
            self.assertIsNone(self.installer.resolve_paths()[1].ASAR)
            code, _, errors = self.main("install")
        self.assertEqual(code, 1)
        self.assertIn("未找到有效 ZCode", errors)
        self.assertEqual(foreign.read_bytes(), before)
        self.assertEqual(self.config(), {"asar_path": str(foreign)})
        self.pi.launch_monitor.assert_not_called()

    def test_invalid_environment_and_stale_config_fall_back_to_valid_candidate(self):
        foreign = self.root / "foreign.asar"
        self.write_asar(foreign, name="electron")
        os.environ["ZCODE_ASAR"] = str(foreign)
        self.write_config({"asar_path": str(self.root / "removed" / "app.asar"), "lang": "en"})
        with mock.patch.object(self.installer, "asar_candidates", return_value=[self.asar]):
            code, _, _ = self.main("install")
        self.assertEqual(code, 0)
        self.assertEqual(self.config()["asar_path"], str(self.asar))
        self.assertEqual(self.config()["lang"], "en")
        self.assertEqual(self.pi.entry_bytes_of(self.asar), self.body + self.pi.INJECT_LINE.encode())

    def test_no_target_never_reuses_cached_patch_module_target(self):
        os.environ["ZUSAGE_PLUGIN_ASAR"] = str(self.asar)
        self.assertEqual(self.installer.resolve_paths()[1].ASAR, self.asar)
        del os.environ["ZUSAGE_PLUGIN_ASAR"]
        self.assertIsNone(self.installer.resolve_paths()[1].ASAR)
        for name in ("BAK", "TMP", "ZCODE_EXE"):
            self.assertIsNone(getattr(self.pi, name))
        self.assertIsNone(self.installer.injected_status(self.pi))
        self.assertFalse((self.runtime / "config.json").exists())

    def test_unpacked_missing_or_invalid_package_is_not_a_production_target(self):
        for name, unpacked in (("zcode", True), (None, False), (["zcode"], False)):
            with self.subTest(name=name, unpacked=unpacked):
                self.write_asar(self.asar, name=name, unpacked=unpacked)
                self.assertFalse(self.installer.is_zcode_asar(self.pi, self.asar))
        self.asar.write_bytes(b"not an asar")
        self.assertFalse(self.installer.is_zcode_asar(self.pi, self.asar))

    def test_first_install_creates_only_python_and_actual_target_configuration(self):
        os.environ["ZCODE_ASAR"] = str(self.asar)
        self.assertEqual(self.main("install")[0], 0)
        self.assertEqual(self.config(), {"python_path": sys.executable, "asar_path": str(self.asar)})
        self.assertFalse(self.installer.auto_enabled(self.runtime))
        self.assertTrue(self.installer.injected_status(self.pi))
        self.pi.launch_monitor.assert_called_once()
        self.pi.syntax_check.assert_called_once_with(self.pi.TMP)
        self.assertFalse(self.pi.TMP.exists())
        # 后续卸载仅靠已记住的非标准位置，不依赖环境变量或缓存目标。
        del os.environ["ZCODE_ASAR"]
        self.pi.ASAR = self.root / "incorrect-cached.asar"
        self.assertEqual(self.main("remove")[0], 0)
        self.assertEqual(self.pi.entry_bytes_of(self.asar), self.body)

    def test_install_preserves_existing_python_and_unrelated_nested_settings(self):
        settings = {"asar_path": str(self.asar), "python_path": "custom-python", "lang": "en",
                    "remote": {"enabled": True, "host": "my-host"}, "plugin_auto_update": True,
                    "plugin_auto_install_overlay": False, "hot_reload": False}
        self.write_config(settings)
        with mock.patch.object(self.installer, "_atomic_write", wraps=self.installer._atomic_write) as atomic:
            self.assertEqual(self.main("install")[0], 0)
        self.assertEqual(self.config(), settings)
        self.assertNotIn(self.runtime / "config.json", [call.args[0] for call in atomic.call_args_list])

    def test_missing_python_is_added_without_enabling_or_disabling_user_optins(self):
        settings = {"asar_path": str(self.asar), "plugin_auto_install_overlay": True,
                    "plugin_auto_update": False, "remote": {"enabled": False}}
        self.write_config(settings)
        self.assertEqual(self.main("install")[0], 0)
        self.assertEqual(self.config(), dict(settings, python_path=sys.executable))

    def test_corrupt_config_is_never_overwritten_even_after_successful_install(self):
        os.environ["ZCODE_ASAR"] = str(self.asar)
        self.runtime.mkdir(parents=True)
        config_path = self.runtime / "config.json"
        for content in (b"{bad JSON", b"[]", b"null", b'"not an object"'):
            with self.subTest(content=content):
                config_path.write_bytes(content)
                self.assertEqual(self.main("install")[0], 0)
                self.assertEqual(config_path.read_bytes(), content)
                self.assertFalse(self.installer.auto_enabled(self.runtime))
        self.assertTrue(self.installer.injected_status(self.pi))

    def test_config_save_is_atomic_and_failed_replace_preserves_settings(self):
        settings = {"asar_path": str(self.asar), "lang": "en", "remote": {"enabled": True}}
        self.write_config(settings)
        self.installer.resolve_paths()
        config_path = self.runtime / "config.json"
        before = config_path.read_bytes()
        real_replace = os.replace

        def inspect_replace(source, destination):
            self.assertEqual(Path(source).parent, config_path.parent)
            self.assertEqual(destination, config_path)
            self.assertEqual(json.loads(Path(source).read_text(encoding="utf-8")),
                             dict(settings, python_path=sys.executable))
            raise OSError("config replace denied")

        with mock.patch.object(self.installer.os, "replace", side_effect=inspect_replace):
            with self.assertRaisesRegex(OSError, "config replace denied"):
                self.installer.save_runtime_config(self.runtime, self.pi)
        self.assertEqual(config_path.read_bytes(), before)
        self.assertFalse(list(self.runtime.glob(".config.json.*")))
        with mock.patch.object(self.installer.os, "replace", wraps=real_replace) as replace:
            self.assertTrue(self.installer.save_runtime_config(self.runtime, self.pi))
        self.assertEqual(replace.call_count, 1)
        self.assertEqual(self.config(), dict(settings, python_path=sys.executable))

    def test_state_rejects_history_wrong_paths_and_duplicate_injections(self):
        self.pi.set_target(self.asar)
        self.pi.set_runtime(self.runtime)
        current = self.pi.INJECT_LINE.encode()
        old = self.pi.INJECT_LINE_TMPL.format(url=(self.root / "old-plugin/inject-main.cjs").as_uri()).encode()
        for suffix in (b"", old, old + current, current * 2,
                       current.replace(b"inject-main.cjs", b"wrong-loader.cjs")):
            with self.subTest(suffix=suffix):
                self.write_asar(self.asar, entry=self.body + suffix)
                self.assertFalse(self.installer.injected_status(self.pi))
        self.write_asar(self.asar, entry=self.body + current)
        self.assertTrue(self.installer.injected_status(self.pi))
        self.asar.unlink()
        self.assertIsNone(self.installer.injected_status(self.pi))

    def test_state_accepts_exact_bytes_injection_line_stub(self):
        line = b"\n;stub();"
        stub = types.SimpleNamespace(ASAR=self.asar, INJECT_LINE=line,
                                     ZUSAGE_LINE_RE=re.compile(rb"historical-line"),
                                     entry_bytes_of=lambda _: self.body + line)
        self.assertTrue(self.installer.injected_status(stub))
        stub.entry_bytes_of = lambda _: self.body + line * 2
        self.assertFalse(self.installer.injected_status(stub))

    def test_client_upgrade_overwrite_is_repaired_only_with_optin(self):
        self.write_config({"asar_path": str(self.asar)})
        self.assertEqual(self.main("install")[0], 0)
        upgraded = b"export const clientVersion = '2.0.0';\n"
        self.write_asar(self.asar, entry=upgraded, version="2.0.0")
        before = self.asar.read_bytes()
        result, _ = self.hook()
        self.assertIn("需要请运行", result["systemMessage"])
        self.assertEqual(self.asar.read_bytes(), before)
        settings = self.config()
        settings["plugin_auto_install_overlay"] = True
        self.write_config(settings)
        result, _ = self.hook()
        self.assertIn("已自动安装", result["systemMessage"])
        self.assertEqual(result["hookSpecificOutput"]["hookEventName"], "SessionStart")
        self.assertIn("第一行", result["hookSpecificOutput"]["additionalContext"])
        self.assertTrue((self.runtime / ".plugin-restart-required").is_file())
        self.assertEqual(self.pi.entry_bytes_of(self.asar), upgraded + self.pi.INJECT_LINE.encode())
        with mock.patch.object(self.pi, "repack", wraps=self.pi.repack) as repack:
            unchanged, _ = self.hook()
        self.assertEqual(unchanged["hookSpecificOutput"]["hookEventName"], "SessionStart")
        self.assertIn("完全退出 ZCode", unchanged["systemMessage"])
        repack.assert_not_called()
        helper = load_module("upgrade_runtime_lock", PLUGIN_ROOT / "runtime_lock.py")
        hashes = json.loads((self.runtime / ".plugin-restart-required").read_text()).get("hashes", {})
        helper.ack_restart(self.runtime, hashes)
        after_restart, _ = self.hook()
        self.assertEqual(after_restart, {"suppressOutput": True})
        with self.asar.open("rb") as stream:
            header, base = self.pi.read_header(stream)
            node = header["files"]["package.json"]
            stream.seek(base + int(node["offset"]))
            self.assertEqual(json.loads(stream.read(node["size"]))["version"], "2.0.0")
            asset = header["files"]["assets"]["files"]["unchanged.txt"]
            stream.seek(base + int(asset["offset"]))
            self.assertEqual(stream.read(asset["size"]), b"official resource")

    def test_optin_repairs_old_runtime_paths_and_duplicate_lines_using_real_install(self):
        self.write_config({"asar_path": str(self.asar), "plugin_auto_install_overlay": True})
        self.pi.set_runtime(self.runtime)
        current = self.pi.INJECT_LINE.encode()
        old = self.pi.INJECT_LINE_TMPL.format(url=(self.root / "removed-cache/inject-main.cjs").as_uri()).encode()
        for suffix in (old, old + current, current * 2):
            with self.subTest(suffix=suffix):
                self.write_asar(self.asar, entry=self.body + suffix)
                result, _ = self.hook()
                self.assertIn("已自动安装", result["systemMessage"])
                self.assertEqual(self.pi.entry_bytes_of(self.asar), self.body + current)
                self.assertTrue(self.installer.injected_status(self.pi))

    def test_hook_existing_valid_target_backfills_config_without_enabling_auto(self):
        self.pi.set_runtime(self.runtime)
        self.write_asar(self.asar, entry=self.body + self.pi.INJECT_LINE.encode())
        os.environ["ZCODE_ASAR"] = str(self.asar)
        result, _ = self.hook()
        self.assertIn("重启 ZCode", result["systemMessage"])
        self.assertEqual(self.config(), {"python_path": sys.executable, "asar_path": str(self.asar)})
        self.pi.launch_monitor.assert_not_called()

    def test_hook_starts_update_after_sync_even_without_target(self):
        def assert_runtime_ready(runtime, pi):
            self.assertTrue((runtime / "plugin-runtime.json").exists())
            self.assertTrue((runtime / "plugin-updater.cjs").exists())
            self.assertIsNone(pi.ASAR)

        self.updater.start_auto_update.side_effect = assert_runtime_ready
        result, _ = self.hook()
        self.assertEqual(result, {"suppressOutput": True})
        self.updater.start_auto_update.assert_called_once_with(self.runtime, self.pi)
        self.assertFalse((self.runtime / "config.json").exists())

    def test_update_failure_does_not_block_optin_overlay_install(self):
        self.write_config({"asar_path": str(self.asar), "plugin_auto_install_overlay": True})
        self.updater.start_auto_update.side_effect = RuntimeError("update unavailable")
        result, errors = self.hook()
        self.assertIn("已自动安装", result["systemMessage"])
        self.assertIn("update unavailable", errors)
        self.assertTrue(self.installer.injected_status(self.pi))

    def test_hook_starts_update_when_current_injection_is_already_valid(self):
        self.write_config({"asar_path": str(self.asar)})
        self.assertEqual(self.main("install")[0], 0)
        result, _ = self.hook()
        self.assertEqual(result["hookSpecificOutput"]["hookEventName"], "SessionStart")
        self.assertIn("完全退出 ZCode", result["systemMessage"])
        self.updater.start_auto_update.assert_called_once_with(self.runtime, self.pi)
        self.pi.launch_monitor.assert_called_once()


if __name__ == "__main__":
    unittest.main()
