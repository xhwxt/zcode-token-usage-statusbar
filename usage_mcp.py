# -*- coding: utf-8 -*-
"""ZCode token 用量 MCP server（stdio, 零依赖；注册名 zusage）。

提供工具 token_usage(scope)：current(默认)/today/days:N/sessions[:N]/models[:days]/session:<id前缀>。
协议仅实现 initialize / tools/list / tools/call；调试信息只走 stderr。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import zusage  # noqa: E402

PROTOCOL_VERSION = "2024-11-05"


def tool_token_usage(scope: str = "current") -> str:
    con = zusage.connect()
    try:
        if scope in ("current", "now"):
            return zusage.render_current(con)
        if scope == "today":
            return zusage.render_today(con)
        if scope.startswith("days:"):
            return zusage.render_days(con, int(scope.split(":", 1)[1]))
        if scope == "week":
            return zusage.render_days(con, 7)
        if scope.startswith("sessions"):
            n = int(scope.split(":", 1)[1]) if ":" in scope else 10
            return zusage.render_sessions(con, n)
        if scope.startswith("models"):
            d = int(scope.split(":", 1)[1]) if ":" in scope else 7
            return zusage.render_models(con, d)
        if scope.startswith("session:"):
            prefix = scope.split(":", 1)[1]
            rows = con.execute(
                "select id from session where id like ?", (prefix + "%",)
            ).fetchall()
            if not rows:
                m = con.execute(
                    "select distinct session_id from model_usage where session_id like ?",
                    (prefix + "%",),
                ).fetchall()
                rows = [(r[0],) for r in m]
            if not rows:
                return f"未找到 id 前缀为 {prefix!r} 的会话"
            sid = rows[0][0]
            sess = con.execute("select * from session where id=?", (sid,)).fetchone()
            u = zusage.session_usage(con, sid)
            head = f"■ 会话 {sid}"
            if sess is not None:
                head += f"  {sess['title']}"
            return (
                f"{head}\n  {u['turns']} 轮 / {u['requests']} 次请求\n"
                f"  input {zusage.fmt(u['input'])} (cache read {zusage.fmt(u['cache_read'])})  "
                f"output {zusage.fmt(u['output'])}  合计 {zusage.fmt(u['total'])}\n"
                f"  上下文水位 ≈ {zusage.fmt(u['last_request_input'])} tokens"
            )
        return f"未知 scope: {scope!r}。可用: current | today | week | days:N | sessions[:N] | models[:days] | session:<id前缀>"
    finally:
        con.close()


TOOLS = [
    {
        "name": "token_usage",
        "description": (
            "查询 ZCode 的 token 用量（数据来自本地 db.sqlite，只读）。"
            "scope 可选: current(当前会话+今日,默认), today, week, days:N(近N天每日), "
            "sessions:N(最近N个会话), models:days(按模型), session:<id前缀>(指定会话)。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "scope": {
                    "type": "string",
                    "description": "查询范围，默认 current",
                }
            },
        },
    }
]


def reply(req_id, result):
    print(json.dumps({"jsonrpc": "2.0", "id": req_id, "result": result}), flush=True)


def main():
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            continue
        method = msg.get("method", "")
        req_id = msg.get("id")
        if method == "initialize":
            reply(
                req_id,
                {
                    "protocolVersion": msg.get("params", {}).get(
                        "protocolVersion", PROTOCOL_VERSION
                    ),
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "zcode-token-usage", "version": "1.0.0"},
                },
            )
        elif method == "tools/list":
            reply(req_id, {"tools": TOOLS})
        elif method == "tools/call":
            params = msg.get("params", {})
            args = params.get("arguments") or {}
            try:
                text = tool_token_usage(**args)
                reply(req_id, {"content": [{"type": "text", "text": text}], "isError": False})
            except Exception as e:  # noqa: BLE001
                reply(
                    req_id,
                    {
                        "content": [{"type": "text", "text": f"查询失败: {e}"}],
                        "isError": True,
                    },
                )
        elif req_id is not None:
            print(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {"code": -32601, "message": f"unknown method {method}"},
                    }
                ),
                flush=True,
            )


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    main()
