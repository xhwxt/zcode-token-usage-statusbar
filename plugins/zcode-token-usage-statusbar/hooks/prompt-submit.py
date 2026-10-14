#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""UserPromptSubmit hook：每次用户发消息时
1) 记录"用户最后所处的会话"与提问时间戳到状态文件（--current 守卫依赖该时间戳）
2) 注入上一轮速率作为模型上下文，并下达"本问统计"指令：
   模型在回答收尾时运行 token_rate.py --turn --current，把输出的统计行
   原样引用在回复末尾。--current 保证绝不把上一轮数据冒充本问（纯问答轮无输出）。
⚙ 面板开关：运行时 config.json 的 answer_rate_line（缺省=开启）；关闭后每次提问
   仍记录时刻（守卫数据保持新鲜），但不再注入统计指令——下一问即生效，无需重启。
输出必须为严格 JSON（多余键会校验失败被丢弃）。
"""
import json
import os
import sys
import threading
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent


def read_stdin(timeout_s=2.0):
    """消费 stdin（协议要求），设超时兜底：客户端不给 EOF 也不挂起。内容不使用。"""
    result = {"raw": ""}

    def pump():
        try:
            result["raw"] = sys.stdin.read()
        except Exception:
            pass

    t = threading.Thread(target=pump, daemon=True)
    t.start()
    t.join(timeout_s)
    return result["raw"]


# 本问统计指令：模型在写最终回复之前自测当前提问的速率，统计行与回复正文同处最后一条消息。
# 防折叠约束：客户端把带工具调用的消息折叠进「模型轨迹」区，只有轮次最后一条纯文字消息
# 默认展开——若模型先写完正文再补跑脚本、让统计行单独成条，用户就只能看到一行统计。
RATE_SCRIPT = PLUGIN_ROOT / "token_rate.py"
TURN_STATS_INSTRUCTION = [
    "",
    "【本轮统计指令】若你在本次回答中已经调用过工具,请严格按以下顺序收尾:",
    f"1. 在开始写最终回复正文之前,运行一次:python3 \"{RATE_SCRIPT}\" --turn --current",
    "2. 然后输出完整的最终回复正文(用户需要的全部内容),并把脚本输出的那一行原样放入 Markdown 引用块(新行行首加「> 」)附在这条回复的最末尾;不要改写数字、不要追加任何其他内容。",
    "关键约束:统计行必须与完整回复正文在同一条消息里。绝不能让统计行(或只含统计行的短消息)单独成为你最后一条消息,也绝不能在写完全部回复正文之后再调用任何工具——客户端会折叠之前带工具调用的消息,用户将只能看到一行统计。",
    "降级规则:若你已经写完回复正文才发现没运行统计脚本,就直接省略统计行结束回答,不要事后补调用工具。",
    "注意:①上面【内部背景·勿展示】行是上一条提问的历史数据,仅供你了解背景,绝不展示给用户;②若脚本没有输出任何统计行,或本次回答你没有调用过任何工具,则不要显示任何统计行、也不要为此额外调用工具。",
]


def emit(ctx):
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": ctx,
        }
    }, ensure_ascii=False))


def rate_line_enabled():
    """⚙ 面板开关：运行时 config.json 的 answer_rate_line（缺省=开启）。

    运行时目录与 overlay_install.default_runtime() 同源，含 ZUSAGE_PLUGIN_RUNTIME 测试接缝；
    文件缺失/损坏一律视为开启（开关失效不吞功能）。"""
    runtime = Path(os.environ.get("ZUSAGE_PLUGIN_RUNTIME")
                   or Path.home() / ".zcode" / "zcode-token-usage-statusbar")
    try:
        cfg = json.loads((runtime / "config.json").read_text(encoding="utf-8"))
    except Exception:
        return True
    return not (isinstance(cfg, dict) and cfg.get("answer_rate_line") is False)


def main():
    read_stdin()
    enabled = rate_line_enabled()   # 先判开关：关闭时不再开库查"上轮行"（白查还占锁）
    try:
        sys.path.insert(0, str(PLUGIN_ROOT))
        import token_rate
        sid = os.environ.get("ZCODE_SESSION_ID") or os.environ.get("CLAUDE_SESSION_ID") or ""
        if sid:
            token_rate.write_session_state(sid, "prompt-submit")
        prev = None if not enabled else token_rate.format_line(token_rate.query_latest(sid or None))
    except Exception:
        prev = None
    try:
        from restart_notice import reminder_context
        notice = reminder_context()
    except Exception:
        notice = ""
    try:
        head = (notice + "\n") if notice else ""
        if not enabled:
            emit(head)   # 关：只留重启提醒（若有），不下达统计指令、不查库
            return
        emit(head + "【内部背景·勿展示】上一条回复:" + (prev or "暂无已完成的模型请求")
             + "\n" + "\n".join(TURN_STATS_INSTRUCTION))
    except Exception:
        emit("")


if __name__ == "__main__":
    main()
