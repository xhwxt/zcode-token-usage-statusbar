"""Isolated regression fixtures; never writes to the user's database/config."""
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import zusage


class UsageBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.row_factory = sqlite3.Row
        self.addCleanup(self.con.close)
        self.con.execute("CREATE TABLE session(id TEXT, title TEXT, directory TEXT, parent_id TEXT)")
        self.con.execute("""CREATE TABLE model_usage(session_id TEXT, status TEXT, completed_at INTEGER,
            input_tokens INTEGER, output_tokens INTEGER, cache_read_input_tokens INTEGER,
            computed_total_tokens INTEGER, duration_ms INTEGER, reasoning_tokens INTEGER,
            retry_count INTEGER, cache_creation_input_tokens INTEGER, model_id TEXT, query_source TEXT)""")

    def test_midnight_belongs_to_only_one_day(self):
        self.con.execute("INSERT INTO model_usage(status, completed_at, computed_total_tokens) VALUES('completed', 86400000, 100)")
        before = zusage.range_usage(self.con, 0, 86400000)
        after = zusage.range_usage(self.con, 86400000, 172800000)
        self.assertEqual((before[0], after[0]), (0, 1))
        self.assertEqual(before[4] + after[4], 100)

    def test_ambiguous_prefix_is_rejected(self):
        self.con.executemany("INSERT INTO session(id) VALUES(?)", [("sess_a",), ("sess_b",)])
        with self.assertRaises(zusage.UsageArgumentError):
            zusage.render_session_detail(self.con, "sess_")

    def test_orphan_usage_prefix_is_also_checked(self):
        self.con.executemany("INSERT INTO model_usage(session_id) VALUES(?)", [("old_a",), ("old_b",)])
        with self.assertRaises(zusage.UsageArgumentError):
            zusage.render_session_detail(self.con, "old_")

    def test_exact_id_and_literal_wildcards(self):
        ids = ("sess_a", "sess_ab", "literal%id", "literal_id", "literalXid", "slash\\id")
        self.con.executemany("INSERT INTO session(id) VALUES(?)", [(sid,) for sid in ids])
        totals = dict.fromkeys(("turns", "requests", "input", "cache_read", "output", "total", "last_request_input"), 0)
        with patch.object(zusage, "session_usage", return_value=totals) as usage:
            for prefix, expected in (("sess_a", "sess_a"), ("literal%", "literal%id"),
                                     ("literal_", "literal_id"), ("slash\\", "slash\\id")):
                zusage.render_session_detail(self.con, prefix)
                self.assertEqual(usage.call_args.args[1], expected)
            usage.reset_mock()
            zusage.render_session_detail(self.con, "%")
            usage.assert_not_called()


class ContextWindowTests(unittest.TestCase):
    def test_provider_limits_and_config_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            data = {"provider": {"one": {"models": {"model": {"limit": {"context": 175616}}}},
                                 "two": {"models": {"model": {"limit": {"context": 1000000}}}}}}
            path.write_text(json.dumps(data))
            with patch.object(zusage, "_CONTEXT_CONFIG_PATHS", (path,)), patch.object(zusage, "load_config", return_value={}):
                self.assertEqual(zusage.lookup_context_window("model", "one"), 175616)
                self.assertEqual(zusage.lookup_context_window("model", "two"), 1000000)
                self.assertIsNone(zusage.lookup_context_window("model"))
                data["provider"]["one"]["models"]["model"]["limit"]["context"] = 9999
                path.write_text(json.dumps(data))
                self.assertEqual(zusage.lookup_context_window("model", "one"), 9999)
                path.unlink()
                self.assertIsNone(zusage.lookup_context_window("model", "one"))

    def test_legacy_catalog_uses_saved_install_path(self):
        with tempfile.TemporaryDirectory() as directory:
            catalog = Path(directory) / "model-providers"
            catalog.mkdir()
            (catalog / "models_catalog.json").write_text(json.dumps({"providers": [
                {"models": [{"id": "model", "contextWindow": 32000}]}]}))
            with patch.object(zusage, "_CONTEXT_CONFIG_PATHS", ()), patch.object(
                    zusage, "load_config", return_value={"asar_path": str(Path(directory) / "app.asar")}):
                self.assertEqual(zusage.lookup_context_window("model"), 32000)


if __name__ == "__main__":
    unittest.main()
