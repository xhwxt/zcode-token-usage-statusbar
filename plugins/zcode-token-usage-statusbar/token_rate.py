#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""逐问 token 输出速率：从 ZCode 自身 usage 数据库（model_usage 表）计算真实生成速率。

用法：
  python3 token_rate.py                  最近一次请求 + 会话统计（人类可读）
  python3 token_rate.py --turn           最新一问（本次提问触发的轮次）即时速率
  python3 token_rate.py --turn --current
                                         同上，但本问尚无入库数据时输出为空（--current 守卫，
                                         绝不把上一轮数据当作本问返回）
  python3 token_rate.py --json           JSON 输出
  ZCODE_SESSION_ID=xxx python3 ...       只统计指定会话
  ZCODE_USAGE_DB=/path/db.sqlite         指定数据库路径（默认按用户主目录解析）
  ZUSAGE_RATE_STATE_FILE=/path           指定钩子状态文件（测试接缝）

只读打开 WAL 数据库，不影响运行中的客户端。
速率口径（2026-10-14 据真库 raw_usage_json 与思考段时间分布裁决）：
  分子 = output_tokens（ZCode 的该列已包含思考 token，provider 原生 totalTokens
  = input + output 不另加 reasoning，不能再相加）；
  分母 = 纯生成时长——无思考段 completed_at − first_token_at，含思考段
  completed_at − started_at（思考 token 在 first_token_at 之前流出，窗口须前移），
  按段累加，段间工具等待不计入。
"""
import json
import math
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

from runtime_lock import file_lock

DB_PATH = os.environ.get("ZCODE_USAGE_DB") or str(
    Path.home() / ".zcode" / "cli" / "db" / "db.sqlite")
# 钩子状态文件：记录"用户最后所处的会话"与最近提问时刻（--current 守卫依赖 promptTs）
STATE_FILE = os.environ.get("ZUSAGE_RATE_STATE_FILE") or str(
    Path.home() / ".zcode" / "zusage-rate.last-session.json")
STATE_TTL_MS = 7 * 24 * 3600 * 1000  # 过旧的状态文件视为失效
N = 5                 # 上轮行的统计窗口（均/峰）
MIN_GEN_MS = 200      # 有效样本：最短生成耗时
MAX_GEN_MS = 3_600_000  # 有效样本：最长生成耗时（1h）


def valid_state_ts(value, now=None):
    now = time.time() * 1000 if now is None else now
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and 0 <= now - value <= STATE_TTL_MS)


def read_state():
    try:
        st = json.loads(Path(STATE_FILE).read_text(encoding="utf-8"))
    except Exception:
        return None
    return st if isinstance(st, dict) and valid_state_ts(st.get("ts")) else None


def write_session_state(session_id, source):
    """持久文件锁内合并每会话提问时刻，再原子替换；会话启动不伪造提问。"""
    path = Path(STATE_FILE)
    tmp = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with file_lock(str(path) + ".lock"):
            now = int(time.time() * 1000)
            try:
                prev = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                prev = None
            prompts = prev.get("prompts") if isinstance(prev, dict) else None
            # 迁移仅含全局字段的旧状态，且不把 SessionStart 的 ts 当提问。
            if not isinstance(prompts, dict):
                prompts = {}
                if isinstance(prev, dict) and prev.get("sessionId") \
                        and valid_state_ts(prev.get("promptTs"), now):
                    prompts[prev["sessionId"]] = prev["promptTs"]
            prompts = {k: v for k, v in prompts.items() if valid_state_ts(v, now)}
            if source == "prompt-submit":
                prompts[session_id] = max(prompts.get(session_id, 0), now)
            data = {"sessionId": session_id, "ts": now, "source": source, "prompts": prompts}
            if session_id in prompts:
                data["promptTs"] = prompts[session_id]
            fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(data, stream)
            os.replace(tmp, path)
    except Exception:
        pass
    finally:
        if tmp is not None:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass


def open_db():
    """只读连接；库不存在/损坏返回 None（连接 ro URI 失败不会新建空库）。"""
    try:
        con = sqlite3.connect(f"file:{Path(DB_PATH).as_posix()}?mode=ro", uri=True, timeout=3)
        con.row_factory = sqlite3.Row
        return con
    except sqlite3.OperationalError:
        return None


def fallback_session_id(db):
    """未显式指定会话时：状态文件里"用户最后所处的会话"（切会话即跟随）→ 全局最近完成的会话。"""
    st = read_state()
    if st and st.get("sessionId"):
        return st["sessionId"]
    if db is None:
        return None
    row = db.execute(
        "select session_id from model_usage where status='completed'"
        " order by completed_at desc limit 1").fetchone()
    return row[0] if row else None


def scope_for(db, sid):
    """有 main_turn 数据时只统计 main_turn，否则回退为该会话全部请求。"""
    cols = ("model_id, output_tokens, reasoning_tokens, input_tokens,"
            " cache_read_input_tokens, started_at, first_token_at, completed_at,"
            " time_to_first_token_ms, status")
    base = f"select {cols} from model_usage where status='completed' and query_source='main_turn'"
    tail = " and session_id = ?" if sid else ""
    args = [sid] if sid else []
    if db is not None:
        try:
            has_main = db.execute(base + tail + " limit 1", args).fetchone()
        except sqlite3.OperationalError:
            has_main = None
        if not has_main:
            base = base.replace(" and query_source='main_turn'", "")
    return base + tail, args


def to_item(r):
    # 口径（2026-10-14 据真库 raw_usage_json 裁决，与 Changlan4/zcode-tps-widget 核对一致）：
    # 1) ZCode 的 output_tokens **已包含** reasoning_tokens（provider 原生 totalTokens =
    #    input + output 不加 reasoning），分子直接用 output_tokens，绝不能再加一次思考。
    # 2) 思考 token 在 first_token_at **之前**流出（思考段 ttft/gap 远大于非思考段，
    #    量级闭环 ≈ 思考 token 数 ÷ 额外 gap = 真实速率），所以含思考段的生成窗口要用
    #    completed_at − started_at，否则思考时长被漏出分母、速率虚高。
    tok = r["output_tokens"] or 0
    reasoning = r["reasoning_tokens"] or 0
    ft, ct, st = r["first_token_at"], r["completed_at"], r["started_at"]
    has_time = isinstance(ct, int) and ct > (ft if isinstance(ft, int) else ct)
    gen_ms = ct - ft if has_time else None
    if reasoning > 0 and isinstance(st, int) and ct > st:
        gen_ms = ct - st   # 含思考段：窗口前移到 started_at，把思考期计入生成时长
    valid = gen_ms is not None and MIN_GEN_MS <= gen_ms < MAX_GEN_MS and tok > 0
    ttft = r["time_to_first_token_ms"]
    return {
        "model": r["model_id"],
        "outputTokens": tok,
        "reasoningTokens": reasoning,
        "inputTokens": r["input_tokens"] or 0,
        "cacheRead": r["cache_read_input_tokens"] or 0,
        "ttftMs": ttft if isinstance(ttft, int) else None,
        "genMs": gen_ms,
        "tokPerSec": _round1(tok / gen_ms * 1000) if valid else None,
        "completedAt": ct,
    }


def session_aggregate(db, scope_sql, args, rated):
    """会话累计用独立 SUM（不受窗口限制）；均/峰由已评分窗口计算。"""
    if db is None:
        return None
    row = db.execute(
        f"select count(*), coalesce(sum(output_tokens),0), coalesce(sum(reasoning_tokens),0),"
        f" coalesce(sum(input_tokens),0), coalesce(sum(cache_read_input_tokens),0)"
        f" from ({scope_sql})", args).fetchone()
    return {
        "samples": len(rated),
        "requests": row[0],
        "avg": _round1(sum(i["tokPerSec"] for i in rated) / len(rated)) if rated else None,
        "max": max(i["tokPerSec"] for i in rated) if rated else None,
        "min": min(i["tokPerSec"] for i in rated) if rated else None,
        "totalOutput": row[1],
        "totalReasoning": row[2],
        "totalInput": row[3],
        "totalCacheRead": row[4],
    }


def query_latest(session_id=None):
    """最近一次完成请求 + 会话统计（prompt-submit 注入的「上轮」行用）。"""
    db = open_db()
    try:
        sid = session_id or fallback_session_id(db)
        scope_sql, args = scope_for(db, sid)
        if db is None:
            items, rated, session = [], [], None
        else:
            rows = db.execute(scope_sql + " order by completed_at desc limit ?", args + [N]).fetchall()
            items = [to_item(r) for r in rows]
            rated = [i for i in items if i["tokPerSec"] is not None]
            session = session_aggregate(db, scope_sql, args, rated)
        latest = (rated[0] if rated else None) or (items[0] if items else None)
        return {"sessionId": sid, "latest": latest, "session": session, "history": items[::-1]}
    finally:
        if db is not None:
            db.close()


# 一次用户消息的各段共享 turn_id。按整轮最早 started_at 排序，不能用完成时间：
# 上轮迟到的完成/工具后续段可能比本轮更晚，但轮次身份仍属上轮。
def latest_turn_id(db, sid, prompt_ts=None):
    if db is None:
        return None
    sql = ("select turn_id from model_usage where session_id=? and query_source='main_turn'"
           " and turn_id is not null and turn_id != '' group by turn_id")
    args = [sid]
    if prompt_ts is not None:
        sql += " having min(started_at) >= ?"
        args.append(prompt_ts)
    try:
        row = db.execute(sql + " order by min(started_at) desc, turn_id desc limit 1", args).fetchone()
    except sqlite3.OperationalError:
        return None
    return row[0] if row else None


def last_prompt_ts(session_id=None, state=None):
    """仅接受本会话的有效提问时刻；映射缺项不借别的会话/SessionStart 的时刻。"""
    st = read_state() if state is None else state
    if not st:
        return None
    sid = session_id or st.get("sessionId")
    prompts = st.get("prompts")
    if isinstance(prompts, dict):
        ts = prompts.get(sid)
    elif st.get("sessionId") == sid:
        ts = st.get("promptTs")
    else:
        ts = None
    return ts if valid_state_ts(ts) else None


def query_turn(session_id=None, current=False):
    db = open_db()
    try:
        sid = session_id or fallback_session_id(db)
        if not sid:
            return {"sessionId": None, "turnId": None, "turn": None, "session": None}
        scope_sql, args = scope_for(db, sid)
        session = None
        if db is not None:
            win = db.execute(scope_sql + " order by completed_at desc limit ?", args + [N]).fetchall()
            rated = [i for i in map(to_item, win) if i["tokPerSec"] is not None]
            session = session_aggregate(db, scope_sql, args, rated)
        prompt_ts = last_prompt_ts(sid) if current else None
        if current and prompt_ts is None:
            return {"sessionId": sid, "turnId": None, "turn": None,
                    "noCurrentTurnData": True, "currentTurnUnavailableReason": "missingPromptState", "session": session}
        turn_id = latest_turn_id(db, sid, prompt_ts)
        if not turn_id or db is None:
            result = {"sessionId": sid, "turnId": turn_id, "turn": None, "session": session}
            if current:
                has_identity = db is not None and latest_turn_id(db, sid) is not None
                result.update(noCurrentTurnData=True,
                              currentTurnUnavailableReason="noMatchingTurn" if has_identity else "missingTurnIdentity")
            return result
        rows = db.execute(scope_sql + " and turn_id = ? order by started_at asc, completed_at asc",
                          args + [turn_id]).fetchall()
        if not rows:
            return {"sessionId": sid, "turnId": turn_id, "turn": None, "session": session}
        items = [to_item(r) for r in rows]
        rated = [i for i in items if i["tokPerSec"] is not None]
        total_tok = sum(i["outputTokens"] for i in rated)   # output 已含思考，不再相加
        gen_ms = sum(i["genMs"] for i in rated)
        turn = {
            "requests": len(items),
            "rated": len(rated),
            "ttftMs": items[0]["ttftMs"],  # 本轮第一段的首字延迟
            "firstAt": items[0]["completedAt"],
            "lastAt": max(i["completedAt"] for i in items),
            "genMs": gen_ms,
            "totalOutput": sum(i["outputTokens"] for i in items),
            "totalReasoning": sum(i["reasoningTokens"] for i in items),
            # 本轮即时速率：总产出 / 总纯生成时长（按段加权，排除段间工具等待）
            "tokPerSec": _round1(total_tok / gen_ms * 1000) if gen_ms >= MIN_GEN_MS and total_tok > 0 else None,
            "peak": _round1(max(i["tokPerSec"] for i in rated)) if rated else None,
        }
        return {"sessionId": sid, "turnId": turn_id, "turn": turn, "session": session}
    finally:
        if db is not None:
            db.close()


# 紧凑单位（注入行等需一眼扫读处）：千以下原始、千~万一位小数 k、万~百万取整 k、百万以上一位小数 M
def fmt_compact(n):
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 10_000:
        return f"{round(n / 1000)}k"
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


def fmt_num(n):
    return f"{n:,}"


def _hhmmss(ms):
    return time.strftime("%H:%M:%S", time.localtime(ms / 1000))


def _sec(ms):
    return f"{ms / 1000:.1f}" if isinstance(ms, (int, float)) else "-"


def _round1(x):
    """一位小数；整数值去掉 .0（JS Math.round 的显示口径：400 而非 400.0）。"""
    r = round(x, 1)
    return int(r) if r == int(r) else r


# 本问统计行（回复收尾自测用）。调用方判定 turn 为空时（含 --current 守卫）不显示任何行。
def format_turn_line(r):
    t = r.get("turn")
    if not t:
        return "暂无本轮请求记录"
    parts = [
        f"⚡ {t['tokPerSec'] if t['tokPerSec'] is not None else '-'} tok/s(本轮)",
        f"首字 {_sec(t['ttftMs'])}s",
        f"输出 {fmt_num(t['totalOutput'])}"
        f"{'(含 ' + fmt_num(t['totalReasoning']) + ' 思考)' if t['totalReasoning'] else ''}"
        f" tok / 生成 {_sec(t['genMs']) if t['genMs'] > 0 else '-'}s",
    ]
    if t["requests"] > 1:
        parts.append(f"{t['requests']} 段 / 峰 {t['peak'] if t['peak'] is not None else '-'}")
    if r.get("session"):
        s = r["session"]
        parts.append(f"累计 {fmt_compact(s['totalOutput'])} tok")
    parts.append(f"⏱ {_hhmmss(t['lastAt'])}")
    return " · ".join(parts)


# 上轮行（prompt-submit 注入、CLI 默认输出用）
def format_line(r):
    l = r.get("latest")
    if not l:
        return "暂无已完成的模型请求"
    parts = [
        f"⚡ {l['tokPerSec'] if l['tokPerSec'] is not None else '-'} tok/s(上轮)",
        f"首字 {_sec(l['ttftMs'])}s",
        f"输出 {fmt_num(l['outputTokens'])}"
        f"{'(含 ' + fmt_num(l['reasoningTokens']) + ' 思考)' if l['reasoningTokens'] else ''}"
        f" tok / 生成 {_sec(l['genMs'])}s",
    ]
    s = r.get("session")
    if s:
        if s["samples"]:
            parts.append(f"近{s['samples']}次均 {s['avg']} / 峰 {s['max']}")
        parts.append(f"累计 {fmt_compact(s['totalOutput'])} tok")
    parts.append(f"⏱ {_hhmmss(l['completedAt'])}")
    return " · ".join(parts)


def main(argv):
    json_out = "--json" in argv
    sid = os.environ.get("ZCODE_SESSION_ID") or os.environ.get("CLAUDE_SESSION_ID") or None
    if "--turn" in argv:
        r = query_turn(sid, current="--current" in argv)
        if json_out:
            print(json.dumps(r, ensure_ascii=False))
        elif r.get("turn"):
            print(format_turn_line(r))
        # --current 且本问尚无数据：不输出任何行，调用方据此不显示统计（绝不回退到上一轮）
    else:
        r = query_latest(sid)
        if json_out:
            print(json.dumps(r, ensure_ascii=False))
        else:
            print(format_line(r))
            s = r.get("session")
            if s:
                print(f"会话累计:输出 {fmt_num(s['totalOutput'])}"
                      f"{'(含 ' + fmt_num(s['totalReasoning']) + ' 思考)' if s['totalReasoning'] else ''}"
                      f" tok · 输入 {fmt_num(s['totalInput'])} tok(其中缓存读 {fmt_num(s['totalCacheRead'])})"
                      f" · 请求 {s['requests']} 次")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
