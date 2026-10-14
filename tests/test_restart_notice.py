# -*- coding: utf-8 -*-
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

PLUGIN = Path(__file__).resolve().parents[1] / "plugins" / "zcode-token-usage-statusbar"
spec = importlib.util.spec_from_file_location("restart_notice_test", PLUGIN / "restart_notice.py")
notice = importlib.util.module_from_spec(spec)
spec.loader.exec_module(notice)


class RestartNoticeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.runtime = Path(temporary.name) / "runtime"

    def test_success_records_marker_and_adds_session_context(self):
        output = notice.session_notice(self.runtime, {"suppressOutput": True})
        self.assertEqual(set(output), {"suppressOutput", "systemMessage", "hookSpecificOutput"})
        context = output["hookSpecificOutput"]
        self.assertEqual(context["hookEventName"], "SessionStart")
        self.assertEqual(set(context), {"hookEventName", "additionalContext"})
        self.assertIn("第一行", context["additionalContext"])
        self.assertIn("所有窗口", context["additionalContext"])
        self.assertIn("重开会话无效", context["additionalContext"])
        self.assertGreater(json.loads((self.runtime / notice.MARKER).read_text())["createdAt"], 0)

    def test_no_marker_means_no_reminder(self):
        self.assertEqual(notice.reminder_context(self.runtime), "")

    def test_restart_removes_reminder(self):
        notice.record_restart(self.runtime)
        self.assertNotEqual(notice.reminder_context(self.runtime), "")
        (self.runtime / notice.MARKER).unlink()
        self.assertEqual(notice.reminder_context(self.runtime), "")

    def test_atomic_failure_leaves_previous_marker_unchanged(self):
        notice.record_restart(self.runtime)
        original = (self.runtime / notice.MARKER).read_bytes()
        with mock.patch.object(notice.os, "replace", side_effect=OSError("locked")):
            with self.assertRaises(OSError):
                notice.record_restart(self.runtime)
        self.assertEqual((self.runtime / notice.MARKER).read_bytes(), original)
        self.assertEqual(list(self.runtime.glob(".restart-notice-*")), [])

    def test_sync_markers_merge_pending_hashes_and_legacy_notice_keeps_them(self):
        loader = hashlib.sha256(b"new loader").hexdigest()
        updater = hashlib.sha256(b"new updater").hexdigest()
        overlay = hashlib.sha256(b"new overlay").hexdigest()
        notice.record_restart(self.runtime, {"hashes": {"inject-main.cjs": loader}, "version": "1.1.0"})
        notice.record_restart(self.runtime, {"hashes": {"plugin-updater.cjs": updater}, "version": "1.2.0"})
        notice.record_restart(self.runtime, {"hashes": {"overlay.js": overlay}})
        notice.record_restart(self.runtime)
        marker = json.loads((self.runtime / notice.MARKER).read_text())
        self.assertEqual(marker["hashes"], {"inject-main.cjs": loader, "plugin-updater.cjs": updater,
                                           "overlay.js": overlay})
        self.assertEqual(marker["version"], "1.2.0")
        self.assertGreater(marker["createdAt"], 0)
        self.assertTrue((self.runtime / "plugin-restart.lock").is_file())

    def test_existing_notice_context_does_not_recreate_acknowledged_marker(self):
        notice.record_restart(self.runtime)
        original = (self.runtime / notice.MARKER).read_bytes()
        for _ in range(3):
            self.assertIn("用户可见·重启提醒", notice.reminder_context(self.runtime))
            self.assertEqual((self.runtime / notice.MARKER).read_bytes(), original)
        (self.runtime / notice.MARKER).unlink()
        self.assertEqual(notice.reminder_context(self.runtime), "")
        self.assertFalse((self.runtime / notice.MARKER).exists())

    def test_prompt_hook_preserves_rate_context_and_reminds_until_loader_restart(self):
        notice.record_restart(self.runtime)
        environment = dict(os.environ, HOME=str(self.runtime.parent), USERPROFILE=str(self.runtime.parent),
                           ZUSAGE_PLUGIN_RUNTIME=str(self.runtime), ZCODE_SESSION_ID="sess_notice_test")
        for pending in (True, False):
            if not pending:
                (self.runtime / notice.MARKER).unlink()
            result = subprocess.run([sys.executable, str(PLUGIN / "hooks" / "prompt-submit.py")],
                                    input="{}", capture_output=True, text=True, env=environment,
                                    cwd=str(self.runtime.parent), timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            output = json.loads(result.stdout)
            self.assertEqual(set(output), {"hookSpecificOutput"})
            context = output["hookSpecificOutput"]["additionalContext"]
            self.assertEqual("用户可见·重启提醒" in context, pending)
            self.assertIn("本轮统计指令", context)


if __name__ == "__main__":
    unittest.main()
