# -*- coding: utf-8 -*-
"""重新注入后的会话提醒；标记由客户端 loader 成功加载时清除。"""
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
    marker = (Path(runtime) if runtime is not None else runtime_dir()) / MARKER
    if not marker.is_file():
        return ""
    return ("【用户可见·重启提醒】状态条安装器已成功完成注入，但客户端尚未重新加载 loader。"
            "请在下一条最终回复正文的第一行，用 Markdown 引用块原样提醒用户：\n> " + MESSAGE +
            "\n提醒之后继续回答用户当前问题；不要自行退出或重启客户端。")


def session_notice(runtime, output):
    record_restart(runtime)
    output["systemMessage"] = MESSAGE
    output["hookSpecificOutput"] = {
        "hookEventName": "SessionStart", "additionalContext": reminder_context(runtime)}
    return output
