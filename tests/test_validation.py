"""Regression tests for CLI arguments and MCP tool dispatch."""

import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import usage_mcp  # noqa: E402
import zusage  # noqa: E402


class UsageValidationTests(unittest.TestCase):
    def test_cli_rejects_invalid_limits(self):
        for command in ("days", "sessions", "models", "watch"):
            for value in ("abc", "0", "-3"):
                with self.subTest(command=command, value=value):
                    with patch.object(zusage, "connect", return_value=Mock()):
                        with self.assertRaises(ValueError):
                            zusage.main([command, value])

    def test_blank_session_prefix_never_queries_database(self):
        connection = Mock()
        for prefix in ("", "  "):
            with self.subTest(prefix=prefix):
                with self.assertRaises(ValueError):
                    zusage.render_session_detail(connection, prefix)
        connection.execute.assert_not_called()

    def test_mcp_rejects_unknown_tool_without_running_query(self):
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
             "params": {"name": "nonexistent_tool", "arguments": {"scope": "today"}}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "token_usage", "arguments": {"scope": "today"}}},
        ]
        output = io.StringIO()
        with patch.object(sys, "stdin", io.StringIO("\n".join(map(json.dumps, requests)) + "\n")), \
                patch.object(sys, "stdout", output), \
                patch.object(usage_mcp, "tool_token_usage", return_value="ok") as query:
            usage_mcp.main()
        results = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(results[0]["error"]["code"], -32602)
        self.assertNotIn("result", results[0])
        self.assertEqual(results[1]["result"]["isError"], False)
        query.assert_called_once_with(scope="today")

    def test_mcp_marks_invalid_scopes_as_errors(self):
        requests = [
            {"jsonrpc": "2.0", "id": index, "method": "tools/call",
             "params": {"name": "token_usage", "arguments": {"scope": scope}}}
            for index, scope in enumerate(("days:abc", "sessions:0", "models:-3", "session:", "nonsense", "sessions_typo:1", None, []), 1)
        ]
        output = io.StringIO()
        connection = Mock()
        with patch.object(sys, "stdin", io.StringIO("\n".join(map(json.dumps, requests)) + "\n")), \
                patch.object(sys, "stdout", output), \
                patch.object(zusage, "connect", return_value=connection):
            usage_mcp.main()
        results = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(len(results), len(requests))
        self.assertTrue(all(result["result"]["isError"] for result in results))
        connection.execute.assert_not_called()


    def test_mcp_recovers_after_malformed_requests(self):
        malformed = [None, [], {"jsonrpc": "2.0", "id": 1},
                     {"jsonrpc": "2.0", "id": 2, "method": "initialize", "params": None},
                     {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                      "params": {"name": "token_usage", "arguments": []}}]
        raw = "bad json\n" + "\n".join(map(json.dumps, malformed))
        raw += '\n{"jsonrpc":"2.0","method":"notifications/initialized"}'
        raw += '\n{"jsonrpc":"2.0","id":9,"method":"tools/list"}\n'
        output = io.StringIO()
        with patch.object(sys, "stdin", io.StringIO(raw)), patch.object(sys, "stdout", output):
            usage_mcp.main()
        results = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual([r["error"]["code"] for r in results[:-1]],
                         [-32700, -32600, -32600, -32600, -32602, -32602])
        self.assertEqual(results[-1]["result"]["tools"][0]["name"], "token_usage")

    def test_unknown_scope_does_not_open_database(self):
        with patch.object(zusage, "connect") as connect:
            for scope in ("oops", "sessions_typo:1", "models_typo", None, []):
                with self.assertRaises(zusage.UsageArgumentError):
                    usage_mcp.tool_token_usage(scope)
            connect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
