# -*- coding: utf-8 -*-
"""ZCode token 用量 MCP server（stdio, 零依赖；注册名 zcode-token-usage-statusbar）。

提供工具 token_usage(scope)：current(默认)/today/days:N/sessions[:N]/models[:days]/session:<id前缀>/workspace:<目录关键词>。
协议实现 initialize / ping / tools/list / tools/call；调试信息只走 stderr。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import zusage  # noqa: E402

PROTOCOL_VERSION = "2024-11-05"

L = zusage.L   # 输出语言与 CLI 同源（config.json 的 "lang"）


def tool_token_usage(scope: str = "current") -> str:
    if not isinstance(scope, str):
        raise zusage.UsageArgumentError(L("scope 必须是字符串", "scope must be a string"))
    scope = scope.strip()
    if scope not in ("current", "now", "today", "week", "sessions", "models") and not scope.startswith(
            ("days:", "sessions:", "models:", "session:", "workspace:")):
        choices = "current | today | week | days:N | sessions[:N] | models[:days] | session:<id> | workspace:<keyword>"
        raise zusage.UsageArgumentError(L(f"未知 scope: {scope!r}。可用: {choices}",
                                         f"Unknown scope: {scope!r}. Available: {choices}"))
    con = zusage.connect()
    try:
        if scope in ("current", "now"):
            return zusage.render_current(con)
        if scope == "today":
            return zusage.render_today(con)
        if scope.startswith("days:"):
            return zusage.render_days(con, zusage.positive_int(scope.split(":", 1)[1], "days"))
        if scope == "week":
            return zusage.render_days(con, 7)
        if scope == "sessions" or scope.startswith("sessions:"):
            n = zusage.positive_int(scope.split(":", 1)[1], "sessions") if ":" in scope else 10
            return zusage.render_sessions(con, n)
        if scope == "models" or scope.startswith("models:"):
            d = zusage.positive_int(scope.split(":", 1)[1], "models") if ":" in scope else 7
            return zusage.render_models(con, d)
        if scope.startswith("session:"):
            return zusage.render_session_detail(con, scope.split(":", 1)[1].strip())
        if scope.startswith("workspace:"):
            return zusage.render_workspace(con, scope.split(":", 1)[1].strip())
    finally:
        con.close()


TOOLS = [
    {
        "name": "token_usage",
        "description": (
            L("查询 ZCode 的 token 用量（数据来自本地 db.sqlite，只读）。"
              "scope 可选: current(当前会话+今日,默认), today, week, days:N(近N天每日), "
              "sessions:N(最近N个会话), models:days(按模型), session:<id前缀>(指定会话,含按模型与子代理明细), "
              "workspace:<目录关键词>(按工作区聚合主会话+子代理,含按会话与按模型明细)。",
              "Query ZCode token usage (from the local db.sqlite, read-only). "
              "scope: current(current session+today, default), today, week, days:N(last N days per day), "
              "sessions:N(last N sessions), models:days(by model), session:<id prefix>(specific session, with per-model and subagent details), "
              "workspace:<dir keyword>(aggregate a workspace's main sessions+subagents, with per-session and per-model details).")
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "scope": {
                    "type": "string",
                    "description": L("查询范围，默认 current", "Query scope, default current"),
                }
            },
        },
    }
]


def reply(req_id, result):
    print(json.dumps({"jsonrpc": "2.0", "id": req_id, "result": result}), flush=True)


def error_reply(req_id, code, message):
    print(json.dumps({"jsonrpc": "2.0", "id": req_id,
                      "error": {"code": code, "message": message}}), flush=True)


def main():
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            error_reply(None, -32700, "Parse error")
            continue
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str):
            error_reply(None, -32600, "Invalid request")
            continue
        method = msg["method"]
        req_id = msg.get("id")
        if "id" not in msg:  # Notifications have no response and must not run queries.
            continue
        if req_id is not None and (isinstance(req_id, bool) or not isinstance(req_id, (str, int))):
            error_reply(None, -32600, "Invalid request id")
            continue
        params = msg.get("params", {})
        if not isinstance(params, dict):
            error_reply(req_id, -32602, "params must be an object")
            continue
        if method == "initialize":
            reply(
                req_id,
                {
                    "protocolVersion": params.get(
                        "protocolVersion", PROTOCOL_VERSION
                    ),
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "zcode-token-usage", "version": "1.0.0"},
                },
            )
        elif method == "tools/list":
            reply(req_id, {"tools": TOOLS})
        elif method == "ping":
            reply(req_id, {})
        elif method == "tools/call":
            if params.get("name") != "token_usage":
                error_reply(req_id, -32602, f"unknown tool: {params.get('name')!r}")
                continue
            args = params.get("arguments", {})
            if not isinstance(args, dict) or set(args) - {"scope"}:
                error_reply(req_id, -32602, "arguments must be an object containing only scope")
                continue
            try:
                text = tool_token_usage(**args)
                reply(req_id, {"content": [{"type": "text", "text": text}], "isError": False})
            except Exception as e:  # noqa: BLE001
                reply(
                    req_id,
                    {
                        "content": [{"type": "text", "text": L(f"查询失败: {e}", f"Query failed: {e}")}],
                        "isError": True,
                    },
                )
        else:
            error_reply(req_id, -32601, f"unknown method {method}")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    main()
