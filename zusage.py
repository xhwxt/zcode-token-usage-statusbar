# -*- coding: utf-8 -*-
"""ZCode token 用量查询（只读访问 ~/.zcode/cli/db/db.sqlite）。

既可作 CLI（python zusage.py [now|today|days N|sessions [N]|models [days]|watch [sec]]），
也可被 usage_mcp.py import 为查询库。
"""
import json
import re
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

DB_PATH = Path.home() / ".zcode" / "cli" / "db" / "db.sqlite"
CONFIG_PATH = Path(__file__).parent / "config.json"


def load_config():
    cfg = {"context_window": 128000, "poll_ms": 2000}
    if CONFIG_PATH.exists():
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except Exception:
            pass
    return cfg


def connect():
    """只读连接优先；WAL shm 不可用时退回普通连接（全程只 SELECT）。
    fallback 前先确认文件存在：sqlite3.connect 对不存在的路径会新建空库，
    可能与尚未初始化数据库的 ZCode 产生交互（v32 审查修复）。"""
    try:
        con = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True, timeout=3)
        con.execute("select 1 from model_usage limit 1").fetchone()
    except sqlite3.OperationalError:
        if not DB_PATH.exists():
            raise
        con = sqlite3.connect(str(DB_PATH), timeout=3)
    con.row_factory = sqlite3.Row
    return con


def _ts(ms):
    return datetime.fromtimestamp(ms / 1000) if ms else None


def _day_start(d=None):
    d = d or datetime.now()
    return d.replace(hour=0, minute=0, second=0, microsecond=0)


def _epoch_ms(dt):
    return int(dt.timestamp() * 1000)


def fmt(n):
    n = n or 0
    if n >= 1_000_000:
        return f"{n / 1e6:.2f}M"
    if n >= 1_000:
        return f"{n / 1e3:.1f}K"
    return str(n)


SESSION_TIMEOUT_MS = 30 * 60 * 1000  # 最近30分钟有请求视为活跃会话


_AGG_TTL_MS = 2000   # 聚合短缓存：今日合计/会话池是慢变量，2s 陈旧无感；泵 1.5s 拉一次省约一半全表聚合
_AGG_CACHE = {}


def _cached_agg(key, fn):
    now = int(time.time() * 1000)
    hit = _AGG_CACHE.get(key)
    if hit and now - hit[0] < _AGG_TTL_MS:
        return hit[1]
    val = fn()
    if len(_AGG_CACHE) > 8:
        _AGG_CACHE.clear()   # 键固定只有几个，正常到不了；保险防膨胀
    _AGG_CACHE[key] = (now, val)
    return val


def _scan_session_ids(con):
    """全库 session_id → max(completed_at) 一次分组扫描（731MB 库 ~55ms）。
    current_session 与 recent 池原先各自独立做同样的全表 group by（3 次 118ms），
    合并为一次扫描并挂 2s TTL。"""
    def run():
        return con.execute(
            """select session_id, max(completed_at) as m
               from model_usage group by session_id""").fetchall()
    return _cached_agg("scan", run)


def current_session(con):
    """返回 (session_row_or_None, usage_dict)；usage_dict 为该会话聚合。"""
    rows = _scan_session_ids(con)
    if not rows:
        return None, None
    row = max(rows, key=lambda r: r[1])
    sid, last_at = row[0], row[1]
    active = last_at and (int(time.time() * 1000) - last_at) < SESSION_TIMEOUT_MS
    usage = session_usage(con, sid)
    usage["active"] = bool(active)
    usage["last_activity"] = _ts(last_at)
    return con.execute("select * from session where id=?", (sid,)).fetchone(), usage


def session_usage(con, sid):
    # last_request_input 取最近一次 completed 请求的 input_tokens（= 当前上下文容量）。
    # 不能用 max(input_tokens)：会话压缩后 input 骤降，峰值永远不回落（v28 修复）。
    r = con.execute(
        """select count(*), coalesce(sum(input_tokens),0), coalesce(sum(output_tokens),0),
                  coalesce(sum(reasoning_tokens),0), coalesce(sum(cache_read_input_tokens),0),
                  coalesce(sum(cache_creation_input_tokens),0), coalesce(sum(computed_total_tokens),0),
                  coalesce(sum(tool_call_count),0),
                  (select m2.input_tokens from model_usage m2
                    where m2.session_id = m.session_id and m2.status = 'completed'
                    order by m2.completed_at desc, m2.id desc limit 1),
                  count(distinct turn_id), coalesce(sum(retry_count),0)
           from model_usage m where m.session_id=? and status='completed'""",
        (sid,),
    ).fetchone()
    return {
        "requests": r[0], "input": r[1], "output": r[2], "reasoning": r[3],
        "cache_read": r[4], "cache_write": r[5], "total": r[6], "tool_calls": r[7],
        "last_request_input": r[8] or 0, "turns": r[9], "retries": r[10],
    }


def range_usage(con, start_ms, end_ms):
    # 末尾三列（reasoning/retry/cache_write）是 v30/v31 追加，CLI 老代码按索引 0-4 取值不受影响
    return con.execute(
        """select count(*), coalesce(sum(input_tokens),0), coalesce(sum(output_tokens),0),
                  coalesce(sum(cache_read_input_tokens),0), coalesce(sum(computed_total_tokens),0),
                  coalesce(sum(duration_ms),0), coalesce(sum(reasoning_tokens),0),
                  coalesce(sum(retry_count),0), coalesce(sum(cache_creation_input_tokens),0)
           from model_usage where status='completed' and completed_at between ? and ?""",
        (start_ms, end_ms),
    ).fetchone()


def daily_usage(con, days):
    """近 N 天（含今天），按本地日聚合。返回 [(date, row), ...] 旧→新。"""
    out = []
    for i in range(days - 1, -1, -1):
        d0 = _day_start(datetime.now() - timedelta(days=i))
        r = range_usage(con, _epoch_ms(d0), _epoch_ms(d0 + timedelta(days=1)))
        out.append((d0.strftime("%m-%d %a"), r))
    return out


def recent_sessions(con, limit=10):
    return con.execute(
        """select s.id, coalesce(s.title,'(无标题)'), coalesce(s.directory,''),
                  count(m.id), coalesce(sum(m.input_tokens),0), coalesce(sum(m.output_tokens),0),
                  coalesce(sum(m.computed_total_tokens),0), max(m.completed_at)
           from model_usage m left join session s on s.id = m.session_id
           where m.status='completed'
           group by m.session_id order by max(m.completed_at) desc limit ?""",
        (limit,),
    ).fetchall()


def model_usage(con, days=7):
    since = _epoch_ms(datetime.now() - timedelta(days=days))
    return con.execute(
        """select provider_id, model_id, count(*), coalesce(sum(input_tokens),0),
                  coalesce(sum(output_tokens),0), coalesce(sum(computed_total_tokens),0)
           from model_usage where status='completed' and completed_at >= ?
           group by provider_id, model_id order by sum(computed_total_tokens) desc""",
        (since,),
    ).fetchall()


# ---------- 展示 ----------

def render_current(con):
    sess, u = current_session(con)
    lines = []
    if sess is not None:
        mark = "🟢 活跃" if u["active"] else "⚪ 闲置"
        lines.append(f"■ 当前会话 [{mark}]  {sess['title']}")
        if sess["directory"]:
            lines.append(f"  目录: {sess['directory']}")
        lines.append(
            f"  {u['turns']} 轮 / {u['requests']} 次请求 / {u['tool_calls']} 次工具调用"
        )
        lines.append(
            f"  input {fmt(u['input'])} (其中 cache read {fmt(u['cache_read'])})  "
            f"output {fmt(u['output'])}  合计 {fmt(u['total'])}"
        )
        lines.append(f"  上下文容量 ≈ {fmt(u['last_request_input'])} tokens (最近一次请求输入)")
        if u["last_activity"]:
            lines.append(f"  最后活动: {u['last_activity']:%H:%M:%S}")
        lines.append("")
    today = range_usage(con, _epoch_ms(_day_start()), _epoch_ms(_day_start() + timedelta(days=1)))
    lines.append(f"■ 今日 ({datetime.now():%Y-%m-%d %a})")
    lines.append(
        f"  {today[0]} 次请求  input {fmt(today[1])} (cache read {fmt(today[3])})  "
        f"output {fmt(today[2])}  合计 {fmt(today[4])}"
    )
    return "\n".join(lines)


def render_days(con, days):
    lines = [f"■ 近 {days} 天每日用量 (合计=input+output)"]
    for label, r in daily_usage(con, days):
        bar = "█" * max(0, min(30, int(r[4] / 200_000)))
        lines.append(
            f"  {label}  {r[0]:>4} 次  in {fmt(r[1]):>8}  out {fmt(r[2]):>7}  合计 {fmt(r[4]):>8}  {bar}"
        )
    return "\n".join(lines)


def render_sessions(con, limit=10):
    lines = [f"■ 最近 {limit} 个会话"]
    for sid, title, d, n, inp, outp, total, last in recent_sessions(con, limit):
        t = _ts(last)
        lines.append(
            f"  {t:%m-%d %H:%M}  {fmt(total):>8}  ({fmt(inp)} in / {fmt(outp)} out, {n} 请求)  "
            f"{title[:40]}  [{sid[:13]}]"
        )
    return "\n".join(lines)


def render_models(con, days=7):
    lines = [f"■ 近 {days} 天按模型"]
    for prov, model, n, inp, outp, total in model_usage(con, days):
        lines.append(
            f"  {model:<22} {n:>5} 次  in {fmt(inp):>8}  out {fmt(outp):>7}  合计 {fmt(total):>8}  ({prov})"
        )
    return "\n".join(lines)


# ---------- 机器可读快照（悬浮条数据源） ----------

CATALOG_PATH = Path(r"D:\ZCode\resources\model-providers\models_catalog_china_llm_zcode_2026-06-03.json")


_CTX_WINDOW_CACHE = {}


def lookup_context_window(model_id):
    """按模型 id 在客户端自带模型目录里查 contextWindow；查不到返回 None。带缓存。"""
    want = (model_id or "").strip().lower()
    if not want:
        return None
    if want in _CTX_WINDOW_CACHE:
        return _CTX_WINDOW_CACHE[want]
    result = None
    try:
        cat = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None
    for p in cat.get("providers", []):
        for m in p.get("models", []):
            if str(m.get("id", "")).lower() == want:
                cw = m.get("contextWindow")
                result = int(cw) if cw else None
                break
    _CTX_WINDOW_CACHE[want] = result
    return result


def _prompt_matches(title, prompt):
    """子会话 title 是否为 Agent part prompt 的截断头（ZCode 规则：prompt 前 57 字符 + "..."）。"""
    t = (title or "").strip()
    if not t or not prompt:
        return False
    if t.endswith("..."):
        base = t[:-3]
        return len(base) >= 15 and prompt.startswith(base)
    return prompt.startswith(t)


# 任务名解析缓存（serve 常驻进程内）：like 扫描父会话全部 part 的 data（本会话可达
# 2456 行大 JSON，实测 29ms/次，泵 1.5s 拉一次全付）。失效键 = 父会话 part 的
# 行数+最新 time_updated（索引聚合，<1ms）；普通消息 part 新增也会失效，宁多算不漏算。
_TASK_CACHE = {}


def _sub_agent_tasks(con, sid):
    stamp = con.execute(
        "select count(*), coalesce(max(time_updated),0) from part where session_id=?", (sid,)
    ).fetchone()
    key = (stamp[0], stamp[1])
    hit = _TASK_CACHE.get(sid)
    if hit and hit[0] == key:
        return hit[1]
    sub_rows_titles = {r[0]: r[1] for r in con.execute(
        "select id, title from session where parent_id=?", (sid,))}
    agent_parts = []
    for prow in con.execute(
        """select data, time_created from part
           where session_id=? and data like '%"tool":"Agent"%' order by time_created""",
        (sid,),
    ):
        try:
            o = json.loads(prow[0])
            st = o.get("state") or {}
            inp = st.get("input") or {}
            desc = str(inp.get("description") or "").strip()
            if desc:
                agent_parts.append((desc, str(inp.get("prompt") or "").strip(),
                                    str(st.get("output") or "")))
        except Exception:
            continue   # part.data 解析失败跳过该条，不影响其余
    tasks = {}
    for desc, _prompt, out in agent_parts:
        m = re.search(r"(?:^|\n)agentId:\s*([^\s(]+)", out)
        if m:
            tasks["sess_subagent_" + m.group(1)] = desc
    used = set(tasks)
    for desc, prompt, out in agent_parts:
        if re.search(r"(?:^|\n)agentId:\s*([^\s(]+)", out):
            continue   # 已走回执关联
        cands = [s for s, t in sub_rows_titles.items()
                 if s not in used and _prompt_matches(t, prompt)]
        if len(cands) == 1:
            tasks[cands[0]] = desc
            used.add(cands[0])
    if len(_TASK_CACHE) > 32:   # 防长驻膨胀：会话数有限，正常到不了；超了整表清掉
        _TASK_CACHE.clear()
    _TASK_CACHE[sid] = (key, tasks)
    return tasks


def _light_snapshot(con, sid, cfg):
    """recent 池轻量行（v8 瘦身）：两条聚合 SQL 出齐顶层计数，明细字段给空结构。
    状态条只渲染 1 个会话，recent 长尾行仅作会话切换兜底；兜底候选（force + latest +
    最近活跃前 2）恒为完整快照，轻量行只在极端长尾被兜底选中时降级显示（缺明细不缺计数）。
    sub.list 聚合数字置 0 而非缺失：overlay 的子代理面板/汇总按字段渲染，结构必须完整。"""
    u = session_usage(con, sid)
    srow = con.execute(
        "select title, summary_additions, summary_deletions, summary_files, parent_id from session where id=?",
        (sid,),
    ).fetchone()
    lr = con.execute("select max(completed_at) from model_usage where session_id=?", (sid,)).fetchone()
    now_ms = int(time.time() * 1000)
    return {
        "sid": sid,
        "title": (srow["title"] if srow is not None else "") or "",
        "active": bool(lr[0] and now_ms - lr[0] < SESSION_TIMEOUT_MS),
        "turns": u["turns"], "requests": u["requests"], "input": u["input"],
        "output": u["output"], "reasoning": u["reasoning"], "cache_read": u["cache_read"],
        "cache_write": u["cache_write"], "total": u["total"], "tool_calls": u["tool_calls"],
        "retries": u["retries"], "ctx": u["last_request_input"],
        "updated": (_ts(lr[0]).strftime("%H:%M:%S") if lr[0] else ""),
        "sub": {"requests": 0, "total": 0, "input": 0, "output": 0, "cache_read": 0,
                "reasoning": 0, "cache_write": 0, "active": False, "list": []},
        "tools": {"total": u["tool_calls"], "errors": 0, "list": []},
        "last_turn": dict(_EMPTY_TURN),
        "last": {"duration_ms": 0, "ttft_ms": 0, "model": "", "tps": 0},
        "code": {"add": (srow["summary_additions"] if srow is not None else None),
                 "del": (srow["summary_deletions"] if srow is not None else None),
                 "files": (srow["summary_files"] if srow is not None else None)},
        "ctx_exc": 0,
        "context_window": cfg["context_window"], "context_auto": False,
        "last_at": (lr[0] if lr[0] else 0),
        "is_sub": sid.startswith("sess_subagent"),
        "parent": (srow["parent_id"] if srow is not None else None),
    }


def _session_snapshot(con, sid, cfg):
    """单个会话的完整快照（最新会话与 recent 列表共用一份结构）。"""
    srow = con.execute("select * from session where id=?", (sid,)).fetchone()
    u = session_usage(con, sid)
    last = con.execute(
        """select duration_ms, coalesce(time_to_first_token_ms,0), completed_at, turn_id, model_id,
                  output_tokens, first_token_at
           from model_usage where session_id=? and status='completed'
           order by completed_at desc limit 1""",
        (sid,),
    ).fetchone()
    # 本轮数据源 = turn_usage 表的每轮完整聚合（v30 起，替代从 model_usage 手工按轮求和）：
    # 自带请求/重试/工具/工具错误计数与 reasoning 拆分，一轮一行。
    lt = {"requests": 0, "retries": 0, "tool_calls": 0, "tool_errors": 0, "input": 0,
          "output": 0, "reasoning": 0, "cache_read": 0, "cache_write": 0,
          "total": 0, "duration_ms": 0, "ttft_ms": 0}
    if last and last["turn_id"]:
        t = con.execute(
            """select coalesce(model_request_count,0), coalesce(model_retry_count,0),
                      coalesce(tool_call_count,0), coalesce(tool_error_count,0),
                      coalesce(input_tokens,0), coalesce(output_tokens,0), coalesce(reasoning_tokens,0),
                      coalesce(cache_read_input_tokens,0), coalesce(cache_creation_input_tokens,0),
                      coalesce(computed_total_tokens,0),
                      coalesce(duration_ms,0), coalesce(time_to_first_token_ms,0)
               from turn_usage where session_id=? and turn_id=? and status='completed'""",
            (sid, last["turn_id"]),
        ).fetchone()
        if t:
            lt = {"requests": t[0], "retries": t[1], "tool_calls": t[2], "tool_errors": t[3],
                  "input": t[4], "output": t[5], "reasoning": t[6], "cache_read": t[7],
                  "cache_write": t[8], "total": t[9], "duration_ms": t[10], "ttft_ms": t[11]}
        else:
            # 轮进行中 turn_usage 还没落 completed 行：回退 model_usage 现场聚合，保持实时显示
            r = con.execute(
                """select count(*), coalesce(sum(input_tokens),0), coalesce(sum(output_tokens),0),
                          coalesce(sum(computed_total_tokens),0), coalesce(sum(duration_ms),0),
                          coalesce(sum(cache_read_input_tokens),0), coalesce(sum(reasoning_tokens),0),
                          coalesce(sum(cache_creation_input_tokens),0)
                   from model_usage where session_id=? and turn_id=? and status='completed'""",
                (sid, last["turn_id"]),
            ).fetchone()
            lt = {"requests": r[0], "retries": 0, "tool_calls": 0, "tool_errors": 0,
                  "input": r[1], "output": r[2], "reasoning": r[6], "cache_read": r[5],
                  "cache_write": r[7], "total": r[3], "duration_ms": r[4], "ttft_ms": 0}
    lr = con.execute("select max(completed_at) from model_usage where session_id=?", (sid,)).fetchone()
    active = bool(lr and lr[0] and int(time.time() * 1000) - lr[0] < SESSION_TIMEOUT_MS)
    ctx_auto = lookup_context_window(last["model_id"]) if last else None
    # 生成速度：输出 token ÷ 生成耗时（首 token 之后到完成；无 first_token_at 时退化为总耗时）
    tps = 0
    if last and last["output_tokens"]:
        gen_ms = None
        if last["first_token_at"] and last["completed_at"] and last["completed_at"] > last["first_token_at"]:
            gen_ms = last["completed_at"] - last["first_token_at"]
        elif last["duration_ms"]:
            gen_ms = last["duration_ms"]
        if gen_ms:
            tps = round(last["output_tokens"] / (gen_ms / 1000), 1)
    sub_rows = con.execute(
        """select m.session_id, coalesce(m.agent,''), coalesce(s2.title,''), count(*),
                  coalesce(sum(m.computed_total_tokens),0),
                  coalesce(sum(m.input_tokens),0), coalesce(sum(m.output_tokens),0),
                  coalesce(sum(m.cache_read_input_tokens),0), coalesce(sum(m.reasoning_tokens),0),
                  coalesce(sum(m.cache_creation_input_tokens),0), max(m.completed_at), s2.time_created
           from model_usage m join session s2 on m.session_id = s2.id
           where m.query_source='subagent' and s2.parent_id=? and m.status='completed'
           group by m.session_id order by max(m.completed_at) desc""",
        (sid,),
    ).fetchall()
    # 子代理任务名：Agent 工具调用 part 的 input.description（与右侧"子智能体目录"面板同源）。
    # 主关联用官方回执：完成后 part.state.output 尾部带 "agentId: agent_xxx" 行，
    # 子会话 id 即 "sess_subagent_"+agentId（客户端自己的硬关联，无歧义）。
    # 兜底只给运行中尚未出现回执的条目：prompt 前缀匹配且候选唯一才绑（同前缀多候选宁可
    # 无名，不做时间猜测——历史 part 不带 childSessionId，时间最近邻存在错位风险）。
    sub_tasks = _sub_agent_tasks(con, sid) if sub_rows else {}
    now_ms = int(time.time() * 1000)
    sub = {
        "requests": sum(r[3] for r in sub_rows),
        "total": sum(r[4] for r in sub_rows),
        "input": sum(r[5] for r in sub_rows),
        "output": sum(r[6] for r in sub_rows),
        "cache_read": sum(r[7] for r in sub_rows),
        "reasoning": sum(r[8] for r in sub_rows),
        "cache_write": sum(r[9] for r in sub_rows),
        "active": any(r[10] and now_ms - r[10] < 30000 for r in sub_rows),
        "list": [
            {"sid": r[0], "agent": r[1], "title": r[2], "task": sub_tasks.get(r[0], ""),
             "requests": r[3], "total": r[4],
             "input": r[5], "output": r[6], "cache_read": r[7], "reasoning": r[8],
             "cache_write": r[9], "last": (r[10] or 0),
             "active": bool(r[10] and now_ms - r[10] < 30000)}
            for r in sub_rows
        ],
    }
    # 工具调用明细（tool_usage 表，本会话按工具分组；status=running 的行不计入）
    tool_rows = con.execute(
        """select tool_name, count(*), coalesce(sum(duration_ms),0),
                  sum(case when status='error' then 1 else 0 end)
           from tool_usage where session_id=? and status in ('completed','error')
           group by tool_name order by count(*) desc, tool_name""",
        (sid,),
    ).fetchall()
    tools = {
        "total": sum(r[1] for r in tool_rows),
        "errors": sum(r[3] or 0 for r in tool_rows),
        "list": [
            {"name": r[0], "count": r[1], "duration_ms": r[2], "errors": r[3] or 0}
            for r in tool_rows
        ],
    }
    return {
        "sid": sid,
        "title": srow["title"] if srow is not None else "",
        "active": active,
        "turns": u["turns"] if u else 0,
        "requests": u["requests"] if u else 0,
        "input": u["input"] if u else 0,
        "output": u["output"] if u else 0,
        "reasoning": u["reasoning"] if u else 0,
        "cache_read": u["cache_read"] if u else 0,
        "cache_write": u["cache_write"] if u else 0,
        "total": u["total"] if u else 0,
        "tool_calls": u["tool_calls"] if u else 0,
        "retries": u["retries"] if u else 0,
        "ctx": u["last_request_input"] if u else 0,
        "updated": (_ts(lr[0]).strftime("%H:%M:%S") if lr and lr[0] else ""),
        "sub": sub,
        "tools": tools,
        "last_turn": lt,
        "last": {
            "duration_ms": last["duration_ms"] if last else 0,
            "ttft_ms": last[1] if last else 0,
            "model": last["model_id"] if last else "",
            "tps": tps,
        },
        # 代码变更统计（session 表 summary_* 列，ZCode 目前未填充、全库为 NULL，有值才会显示）
        "code": {
            "add": (srow["summary_additions"] if srow is not None else None),
            "del": (srow["summary_deletions"] if srow is not None else None),
            "files": (srow["summary_files"] if srow is not None else None),
        },
        # 最近一次"上下文超限被拒"的时刻（该请求 status=error，不会进 completed 统计）；
        # 晚于最近成功请求 = 仍处于超限状态，进度条亮红告警
        "ctx_exc": con.execute(
            """select coalesce(max(coalesce(completed_at, started_at)),0)
               from model_usage where session_id=? and context_exceeded=1""",
            (sid,),
        ).fetchone()[0],
        "context_window": ctx_auto or cfg["context_window"],
        "context_auto": ctx_auto is not None,
        "last_at": (lr[0] if lr and lr[0] else 0),
        "is_sub": sid.startswith("sess_subagent"),
        "parent": (srow["parent_id"] if srow is not None else None),
    }


_EMPTY_TURN = {"requests": 0, "retries": 0, "tool_calls": 0, "tool_errors": 0, "input": 0,
               "output": 0, "reasoning": 0, "cache_read": 0, "cache_write": 0,
               "total": 0, "duration_ms": 0, "ttft_ms": 0}


def snapshot(force_sid=""):
    """单次快照：最新会话 + 最近 6+6 会话池 + 今日。
    force_sid：各窗口当前会话 id，逗号分隔（泵按窗口汇总上报：主进程 IPC 映射的焦点会话 +
    overlay localStorage 键兜底），全部强制纳入快照——不在 6+6 池里的会话（新开的、久远的）
    也能被各窗口找到自己的会话并显示（v32 起；v34 改为多窗口多 sid）。"""
    cfg = load_config()
    con = connect()
    try:
        force_sids = []
        for fs in str(force_sid).split(","):
            fs = fs.strip()
            if fs and re.fullmatch(r"[A-Za-z0-9_-]+", fs) and fs not in force_sids:
                force_sids.append(fs)
        today = _cached_agg("today", lambda: range_usage(
            con, _epoch_ms(_day_start()), _epoch_ms(_day_start() + timedelta(days=1))))
        sess, u = current_session(con)
        latest_sid = sess["id"] if sess is not None else None
        if latest_sid:
            base = _session_snapshot(con, latest_sid, cfg)
        else:
            # 空库（无任何 completed 请求）fallback：字段必须与 _session_snapshot 对齐，
            # 否则 snapshot() 顶层读 base["code"]/["tools"]/["ctx_exc"] 直接 KeyError（v32 审查修复）
            base = {"sid": "", "title": "", "active": False, "turns": 0, "requests": 0,
                    "input": 0, "output": 0, "reasoning": 0, "cache_read": 0, "cache_write": 0,
                    "total": 0, "tool_calls": 0, "retries": 0, "ctx": 0, "updated": "",
                    "last_turn": dict(_EMPTY_TURN),
                    "last": {"duration_ms": 0, "ttft_ms": 0, "model": "", "tps": 0},
                    "code": {"add": None, "del": None, "files": None}, "ctx_exc": 0,
                    "tools": {"total": 0, "errors": 0, "list": []},
                    "sub": {"requests": 0, "total": 0, "input": 0, "output": 0, "cache_read": 0,
                            "reasoning": 0, "cache_write": 0, "active": False, "list": []},
                    "context_window": cfg["context_window"], "context_auto": False}
        ids = []
        rows = _scan_session_ids(con)
        normal = sorted((r for r in rows if not str(r[0]).startswith("sess_subagent")),
                        key=lambda r: -r[1])[:6]
        suba = sorted((r for r in rows if str(r[0]).startswith("sess_subagent")),
                      key=lambda r: -r[1])[:6]
        ids = [r[0] for r in normal] + [r[0] for r in suba]
        seen = set()
        ids = [x for x in ids if not (x in seen or seen.add(x))]
        if latest_sid and latest_sid not in ids:
            ids.insert(0, latest_sid)
        for fs in reversed(force_sids):
            if fs not in ids:
                ids.insert(0, fs)
        # recent 池瘦身（v8）：状态条只渲染 1 个会话，完整快照只给兜底候选
        # （force + latest + 最近活跃前 2），其余长尾行走轻量查询（单会话 38ms→2ms）。
        full_n = len(force_sids) + 3
        rec = []
        for i, sid in enumerate(ids):
            if sid == latest_sid:
                rec.append(base)   # base 本就是 latest 的完整快照，不重算第二遍
            elif i < full_n:
                rec.append(_session_snapshot(con, sid, cfg))
            else:
                rec.append(_light_snapshot(con, sid, cfg))
        return {
            "session": base,
            "last_turn": base["last_turn"],
            "last": base["last"],
            "today": {
                "requests": today[0], "input": today[1], "output": today[2],
                "cache_read": today[3], "total": today[4],
                "reasoning": today[6], "retries": today[7], "cache_write": today[8],
            },
            "context_window": base["context_window"],
            "context_auto": base["context_auto"],
            "code": base["code"],
            "ctx_exc": base["ctx_exc"],
            "tools": base["tools"],
            "recent": rec,
        }
    finally:
        con.close()


# ---------- CLI ----------

def serve():
    """常驻查询服务（v8 泵配套）：省每次 ~60-100ms 的解释器启动+模块加载费。
    stdin 每行一个请求（空行 = 无强制会话），stdout 每行一个 JSON 快照。
    stdin EOF = 泵已退出（管道断开），随即退出——孤儿进程自清理。
    每请求 snapshot() 内部新开 sqlite 连接：不持长读事务，WAL checkpoint 不受阻碍。"""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    while True:
        line = sys.stdin.readline()
        if not line:
            return
        try:
            out = json.dumps(snapshot(line.strip()))
        except Exception as e:
            out = json.dumps({"error": str(e)[:200]})   # 单次查询失败不杀常驻进程
        sys.stdout.write(out + "\n")
        sys.stdout.flush()


def main(argv):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    cmd = argv[0] if argv else "now"
    con = connect()
    if cmd == "now":
        print(render_current(con))
    elif cmd == "json":
        # ensure_ascii 默认 True：全部 \u 转义，保证注入 executeJavaScript 时无 U+2028 类语法风险
        # 可选第二参数：强制纳入快照的会话 id，逗号分隔（各窗口当前会话，泵汇总上报）
        force_sid = argv[1] if len(argv) > 1 else ""
        print(json.dumps(snapshot(force_sid)))
    elif cmd == "serve":
        con.close()   # serve 自管连接（每请求新开），退掉 main 预建的这把
        serve()
    elif cmd == "today":
        print(render_today(con))
    elif cmd == "days":
        print(render_days(con, int(argv[1]) if len(argv) > 1 else 7))
    elif cmd == "sessions":
        print(render_sessions(con, int(argv[1]) if len(argv) > 1 else 10))
    elif cmd == "models":
        print(render_models(con, int(argv[1]) if len(argv) > 1 else 7))
    elif cmd == "watch":
        sec = int(argv[1]) if len(argv) > 1 else 5
        while True:
            print("\x1b[2J\x1b[H", end="")
            print(render_current(con), flush=True)
            print(f"\n(每 {sec}s 刷新, Ctrl+C 退出)", flush=True)
            time.sleep(sec)
    else:
        print(__doc__)
    con.close()


def render_today(con):
    today = range_usage(con, _epoch_ms(_day_start()), _epoch_ms(_day_start() + timedelta(days=1)))
    return (
        f"■ 今日 ({datetime.now():%Y-%m-%d %a})\n"
        f"  {today[0]} 次请求  input {fmt(today[1])} (cache read {fmt(today[3])})  "
        f"output {fmt(today[2])}  合计 {fmt(today[4])}"
    )


if __name__ == "__main__":
    main(sys.argv[1:])
