# -*- coding: utf-8 -*-
import json
import os
import stat
from unittest import mock
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import runtime_lock


class ConfigTransactionTests(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "POSIX permissions")
    def test_existing_config_mode_and_owner_survive_atomic_merge(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            config = runtime / "config.json"
            config.write_text('{"plugin_auto_update":false}')
            config.chmod(0o644)
            before = config.stat()
            runtime_lock.update_config(runtime, {"answer_rate_line": False})
            after = config.stat()
            self.assertEqual(stat.S_IMODE(after.st_mode), 0o644)
            self.assertEqual((after.st_uid, after.st_gid), (before.st_uid, before.st_gid))

    def test_installer_returns_config_and_lock_to_user_on_every_update(self):
        import install
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            with mock.patch.object(install, "chown_to_user") as chown:
                install.update_runtime_config(runtime, {"asar_path": "fixture.asar"})
            self.assertEqual(chown.call_args_list,
                             [mock.call(runtime), mock.call(runtime / "config.json"),
                              mock.call(runtime / "plugin-config.lock")])
            config = json.loads((runtime / "config.json").read_text())
            self.assertEqual(config, {"asar_path": "fixture.asar"})

    def test_changes_and_defaults_preserve_explicit_opt_out(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            config = runtime / "config.json"
            config.write_text('{"plugin_auto_update": false, "python_path": "custom"}', encoding="utf-8")
            runtime_lock.update_config(runtime, {"asar_path": "fixture.asar"},
                                       {"python_path": "fallback", "plugin_auto_update": True})
            self.assertEqual(json.loads(config.read_text()), {
                "plugin_auto_update": False, "python_path": "custom", "asar_path": "fixture.asar"})

    def test_corrupt_or_nonobject_config_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            config = runtime / "config.json"
            for value in ("{broken", "[]", "null", "true"):
                config.write_text(value, encoding="utf-8")
                with self.assertRaises(ValueError):
                    runtime_lock.update_config(runtime, {"plugin_auto_update": True})
                self.assertEqual(config.read_text(), value)

    def test_cli_writers_wait_for_lock_and_merge_latest_config(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            config = runtime / "config.json"
            config.write_text('{"plugin_auto_update": true}', encoding="utf-8")
            children = []
            with runtime_lock.file_lock(runtime / "plugin-config.lock"):
                for changes in ({"asar_path": "fixture.asar"}, {"answer_rate_line": False}):
                    child = subprocess.Popen([sys.executable, str(ROOT / "runtime_lock.py"),
                                              "--update-config", str(runtime)], stdin=subprocess.PIPE,
                                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                    child.stdin.write(json.dumps({"changes": changes}))
                    child.stdin.close()
                    child.stdin = None
                    children.append(child)
                config.write_text('{"plugin_auto_update": false, "remote": {"enabled": true}}', encoding="utf-8")
            for child in children:
                out, err = child.communicate(timeout=15)
                self.assertEqual(child.returncode, 0, err)
                self.assertTrue(json.loads(out)["ok"])
            self.assertEqual(json.loads(config.read_text()), {
                "plugin_auto_update": False, "remote": {"enabled": True},
                "asar_path": "fixture.asar", "answer_rate_line": False})


if __name__ == "__main__":
    unittest.main()
