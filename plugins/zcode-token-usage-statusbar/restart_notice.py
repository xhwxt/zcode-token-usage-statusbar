# -*- coding: utf-8 -*-
"""重新注入后的会话提醒；标记由客户端 loader 成功加载时清除。"""
import hashlib
import json
import os
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).parent.resolve()
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))
from runtime_lock import record_restart as _record_restart

MARKER = ".plugin-restart-required"
MESSAGE = "⚠️ 状态条已自动安装/重新注入或运行时已更新，需完全退出 ZCode（所有窗口）并重新打开后才能生效；仅刷新页面或重开会话无效。"


def runtime_dir():
    return Path(os.environ.get("ZUSAGE_PLUGIN_RUNTIME") or
                Path.home() / ".zcode" / "zcode-token-usage-statusbar")


def record_restart(runtime, data=None):
    """同步传目标 hashes；重新注入可无 hashes，合并/确认共用永久 marker 锁。"""
    runtime = Path(runtime)
    runtime.mkdir(parents=True, exist_ok=True)
    return _record_restart(runtime, data or {})


def reminder_context(runtime=None):
    runtime = Path(runtime) if runtime is not None else runtime_dir()
    marker = runtime / MARKER
    if not marker.is_file():
        return ""
    message = "⚠️ 检测到状态条待重启记录；请完全退出 ZCode（所有窗口）并重新打开，仅刷新页面或重开会话无效。"
    detail = "检测到尚未确认加载的状态条更新记录。"
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
        if data.get("state") == "pending-sync":
            hashes = data.get("hashes", {})
            record = json.loads((runtime / "plugin-runtime.json").read_text(encoding="utf-8"))
            complete = (not (runtime / "plugin-runtime-pending.json").exists() and hashes
                        and all(record.get("hashes", {}).get(name) == digest
                                and hashlib.sha256((runtime / name).read_bytes()).hexdigest() == digest
                                for name, digest in hashes.items()))
            if complete:
                detail = "状态条运行时同步已完成，但客户端尚未确认重新加载；这不代表已重新注入 app.asar。"
            else:
                detail = "状态条运行时同步尚未完成，不能确认安装成功。"
                message = "⚠️ 状态条运行时同步尚未完成，请先检查插件日志并重试安装；仅重启不能保证修复。"
        elif data.get("state") == "installed":
            detail = "状态条注入已完成，但客户端尚未确认重新加载。"
            message = MESSAGE
    except (OSError, ValueError, AttributeError, TypeError):
        if (runtime / "plugin-runtime-pending.json").exists():
            detail = "状态条运行时同步尚未完成，不能确认安装成功。"
            message = "⚠️ 状态条运行时同步尚未完成，请先检查插件日志并重试安装；仅重启不能保证修复。"
    return ("【用户可见·重启提醒】" + detail +
            "请在下一条最终回复正文的第一行，用 Markdown 引用块原样提醒用户：\n> " + message +
            "\n提醒之后继续回答用户当前问题；不要自行退出或重启客户端。")


def session_notice(runtime, output):
    record_restart(runtime, {"state": "installed"})
    output["systemMessage"] = MESSAGE
    output["hookSpecificOutput"] = {
        "hookEventName": "SessionStart", "additionalContext": reminder_context(runtime)}
    return output
