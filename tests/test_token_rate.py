#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""token_rate.py / hooks/prompt-submit.py 的测试。

fixture 库模拟一轮「上轮两段 + 本问两段」的 model_usage，覆盖：
  --current 守卫（本问无入库数据时输出为空，绝不拿上一轮充数）
  加权速率（分子=output 已含思考；含思考段窗口=completed−started）、首字延迟取第一段、
  段数/峰值、会话累计（Σoutput，思考已含不另加）
  hook 输出为严格 JSON（hookSpecificOutput.hookEventName=UserPromptSubmit + additionalContext）

运行：python3 -B -m unittest discover -s tests -p 'test_*.py'
"""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PLUGIN = REPO / "plugins" / "zcode-token-usage-statusbar"
RATE = PLUGIN / "token_rate.py"
PROMPT_HOOK = PLUGIN / "hooks" / "prompt-submit.py"

SCHEMA = """
create table model_usage (
  id text primary key,
  session_id text not null,
  turn_id text,
  query_source text not null,
  model_id text not null,
  status text not null,
  started_at integer not null,
  first_token_at integer,
  completed_at integer,
  time_to_first_token_ms integer,
  input_tokens integer not null default 0,
  output_tokens integer not null default 0,
  reasoning_tokens integer not null default 0,
  cache_read_input_tokens integer not null default 0
);
"""

SID = "sess_fixture"


def make_db(path, rows):
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    con.executemany(
        "insert into model_usage values (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    con.commit()
    con.close()


def row(rid, sid, turn, source, started, ft, ttft, completed, inp, out, rea):
    # 列序对应 SCHEMA：(id, session_id, turn_id, query_source, model_id, status,
    #   started_at, first_token_at, completed_at, time_to_first_token_ms, 输入/输出/思考/缓存读)
    return (rid, sid, turn, source, "test-model", "completed",
            started, ft, completed, ttft, inp, out, rea, 0)


class RateTestBase(unittest.TestCase):
    """fixture：提问时刻 T0；上轮 turn_a 两段（T0 前），本问 turn_b 两段（T0 后）。

    口径（2026-10-14 修正）：分子 = output（ZCode 该列已含思考，不另加）；
    含思考段窗口 = completed − started（思考在 first_token 之前流出）。
    turn_b: b1 输出 1200（思考 300）/ 窗口 8.0s → 150；b2 输出 800（思考 200）/ 5.0s → 160
            → 加权 2000 tok / 13.0s = 153.8，峰 160，首字取 b1 = 3.0s
    会话累计(main_turn Σoutput) = 1000+500+1200+800 = 3500 → "3.5k"（思考 600 已含不另加）
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.tmp.name) / "db.sqlite")
        self.state = str(Path(self.tmp.name) / "state.json")
        self.runtime = Path(self.tmp.name) / "runtime"   # hook 的运行时目录接缝（config.json）
        self.runtime.mkdir()
        now = int(time.time() * 1000)
        t0 = now - 60_000  # 提问时刻（状态文件 TTL 7 天内）
        self.t0 = t0
        rows = [
            # 上轮 turn_a：a1 速率 200，a2 速率 150
            row("a1", SID, "turn_a", "main_turn", t0 - 12000, t0 - 9000, 3000, t0 - 4000, 100, 1000, 0),
            row("a2", SID, "turn_a", "main_turn", t0 - 7000, t0 - 5000, 2000, t0 - 1000, 100, 500, 100),
            # 本问 turn_b：工具调用轮的两段
            row("b1", SID, "turn_b", "main_turn", t0 + 1000, t0 + 4000, 3000, t0 + 9000, 200, 1200, 300),
            row("b2", SID, "turn_b", "main_turn", t0 + 15000, t0 + 17500, 2000, t0 + 20000, 200, 800, 200),
            # 非 main_turn 行：会话标题请求，很早完成，不进统计
            row("title", SID, "turn_title", "session_title", t0 - 25000, t0 - 24000, 1000, t0 - 23000, 50, 99999, 0),
            # 子代理会话：不该被统计进 S
            row("sub1", "sess_subagent_x", "turn_s", "subagent", t0 + 5000, t0 + 6000, 1000, t0 + 8000, 10, 88888, 0),
        ]
        make_db(self.db, rows)

    def tearDown(self):
        self.tmp.cleanup()

    def write_state(self, obj):
        Path(self.state).write_text(json.dumps(obj), encoding="utf-8")

    def run_rate(self, *args, sid=SID, state=None):
        env = dict(os.environ, ZCODE_USAGE_DB=self.db, ZCODE_SESSION_ID=sid)
        if state is not None:
            env["ZUSAGE_RATE_STATE_FILE"] = state
        else:
            env["ZUSAGE_RATE_STATE_FILE"] = self.state
        return subprocess.run([sys.executable, str(RATE), *args],
                              capture_output=True, text=True, env=env, timeout=30)


class TestTurnCurrent(RateTestBase):
    def test_turn_current_with_data(self):
        self.write_state({"sessionId": SID, "ts": self.t0, "promptTs": self.t0})
        r = self.run_rate("--turn", "--current", state=self.state)
        self.assertEqual(r.returncode, 0, r.stderr)
        line = r.stdout.strip()
        self.assertIn("153.8 tok/s(本轮)", line)
        self.assertIn("首字 3.0s", line)
        self.assertIn("输出 2,000(含 500 思考) tok / 生成 13.0s", line)
        self.assertIn("2 段 / 峰 160", line)
        self.assertIn("累计 3.5k tok", line)
        self.assertNotIn("(上轮)", line)

    def test_current_guard_blocks_when_no_data_this_turn(self):
        # 守卫场景：提问时刻晚于库里全部行（纯问答轮收尾自测）→ 输出必须为空
        self.write_state({"sessionId": SID, "ts": self.t0 + 60000, "promptTs": self.t0 + 60000})
        r = self.run_rate("--turn", "--current", state=self.state)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "")

    def test_turn_without_current_still_shows_latest(self):
        # 不带 --current（手动排查用）则无守卫，显示最新轮
        self.write_state({"sessionId": SID, "ts": self.t0 + 60000, "promptTs": self.t0 + 60000})
        r = self.run_rate("--turn", state=self.state)
        self.assertIn("tok/s(本轮)", r.stdout)

    def test_no_state_file_skips_guard(self):
        # 状态文件缺失：无提问时刻可用，守卫无法判定（插件路径下 hook 总会先写状态）
        r = self.run_rate("--turn", "--current", state=str(Path(self.tmp.name) / "absent.json"))
        self.assertIn("tok/s(本轮)", r.stdout)

    def test_guard_per_session_prompt_ts(self):
        # 多窗口交替提问：全局 promptTs 已被会话 B 的提问覆盖（晚于 A 全部入库行），
        # 但 A 的守卫必须用 A 自己的提问时刻 → A 本轮数据不被误吞（v1.3.0 修复）
        import sqlite3 as _sl
        con = _sl.connect(self.db)
        con.execute("insert into model_usage values (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    row("o1", "sess_other", "turn_o", "main_turn",
                        self.t0 + 25000, self.t0 + 26000, 1000, self.t0 + 30000, 50, 900, 100))
        con.commit()
        con.close()
        self.write_state({"sessionId": "sess_other", "ts": self.t0 + 60000,
                          "promptTs": self.t0 + 60000, "source": "prompt-submit",
                          "prompts": {SID: self.t0, "sess_other": self.t0 + 60000}})
        r = self.run_rate("--turn", "--current", state=self.state)
        self.assertIn("tok/s(本轮)", r.stdout)
        self.assertIn("累计 3.5k tok", r.stdout)
        # 对照：sess_other 自己刚提问、其入库行全部早于提问时刻 → 必须被守卫拦下（空输出）
        r2 = self.run_rate("--turn", "--current", state=self.state, sid="sess_other")
        self.assertEqual(r2.stdout, "")

    def test_single_segment_has_no_peak_part(self):
        rows = [row("c1", "sess_one", "turn_c", "main_turn",
                    self.t0 + 1000, self.t0 + 2000, 1000, self.t0 + 5000, 10, 600, 0)]
        db2 = str(Path(self.tmp.name) / "one.sqlite")
        make_db(db2, rows)
        state2 = str(Path(self.tmp.name) / "one.json")
        Path(state2).write_text(json.dumps(
            {"sessionId": "sess_one", "ts": self.t0, "promptTs": self.t0}))
        env = dict(os.environ, ZCODE_USAGE_DB=db2, ZCODE_SESSION_ID="sess_one",
                   ZUSAGE_RATE_STATE_FILE=state2)
        r = subprocess.run([sys.executable, str(RATE), "--turn", "--current"],
                           capture_output=True, text=True, env=env, timeout=30)
        line = r.stdout.strip()
        self.assertIn("200 tok/s(本轮)", line)
        self.assertIn("输出 600 tok / 生成 3.0s", line)
        self.assertNotIn("段 / 峰", line)
        self.assertIn("累计 600 tok", line)

    def test_json_output(self):
        self.write_state({"sessionId": SID, "ts": self.t0, "promptTs": self.t0})
        r = self.run_rate("--turn", "--current", "--json", state=self.state)
        data = json.loads(r.stdout)
        self.assertNotIn("noCurrentTurnData", data)
        turn = data["turn"]
        self.assertEqual(turn["requests"], 2)
        self.assertEqual(turn["ttftMs"], 3000)
        self.assertEqual(turn["genMs"], 13000)
        self.assertEqual(turn["totalOutput"], 2000)
        self.assertEqual(turn["totalReasoning"], 500)
        self.assertEqual(turn["tokPerSec"], 153.8)
        self.assertEqual(turn["peak"], 160)
        self.assertEqual(data["session"]["totalOutput"], 3500)
        self.assertEqual(data["session"]["totalReasoning"], 600)


class TestLatestLine(RateTestBase):
    def test_default_output_is_prev_round(self):
        r = self.run_rate(state=self.state)
        lines = r.stdout.strip().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertIn("160 tok/s(上轮)", lines[0])  # 最近完成的 b2（含思考段窗口 5.0s）
        self.assertIn("首字 2.0s", lines[0])
        self.assertIn("输出 800(含 200 思考) tok / 生成 5.0s", lines[0])
        self.assertIn("近4次均 148.3 / 峰 200", lines[0])
        self.assertIn("累计 3.5k tok", lines[0])
        self.assertIn("请求 4 次", lines[1])  # 只数 main_turn

    def test_empty_db_reports_none(self):
        db2 = str(Path(self.tmp.name) / "empty.sqlite")
        make_db(db2, [])
        env = dict(os.environ, ZCODE_USAGE_DB=db2,
                   ZUSAGE_RATE_STATE_FILE=str(Path(self.tmp.name) / "absent.json"))
        r = subprocess.run([sys.executable, str(RATE)], capture_output=True, text=True,
                           env=env, timeout=30)
        self.assertIn("暂无已完成的模型请求", r.stdout)

    def test_missing_db_is_graceful(self):
        env = dict(os.environ, ZCODE_USAGE_DB=str(Path(self.tmp.name) / "no.sqlite"),
                   ZUSAGE_RATE_STATE_FILE=str(Path(self.tmp.name) / "absent.json"),
                   ZCODE_SESSION_ID="sess_none")
        r = subprocess.run([sys.executable, str(RATE)], capture_output=True, text=True,
                           env=env, timeout=30)
        self.assertEqual(r.returncode, 0)
        self.assertIn("暂无已完成的模型请求", r.stdout)


class TestPromptSubmitHook(RateTestBase):
    def run_hook(self, sid=SID):
        # ZUSAGE_PLUGIN_RUNTIME 指向空 fixture 运行时目录：config 缺失 → 开关缺省=开启（密闭）
        env = dict(os.environ, ZCODE_USAGE_DB=self.db, ZCODE_SESSION_ID=sid,
                   ZUSAGE_RATE_STATE_FILE=self.state,
                   ZUSAGE_PLUGIN_RUNTIME=str(self.runtime))
        return subprocess.run([sys.executable, str(PROMPT_HOOK)], input="",
                              capture_output=True, text=True, env=env, timeout=30)

    def test_strict_json_and_state_written(self):
        before = int(time.time() * 1000)
        r = self.run_hook()
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)  # 多余键会让客户端校验失败，这里必须恰好只有协议键
        self.assertEqual(set(out), {"hookSpecificOutput"})
        inner = out["hookSpecificOutput"]
        self.assertEqual(set(inner), {"hookEventName", "additionalContext"})
        self.assertEqual(inner["hookEventName"], "UserPromptSubmit")
        ctx = inner["additionalContext"]
        self.assertIn("【内部背景·勿展示】上一条回复:", ctx)
        self.assertIn("--turn --current", ctx)
        self.assertIn("token_rate.py", ctx)
        self.assertIn("Markdown 引用块", ctx)
        # 状态文件：记录了会话与提问时刻（--current 守卫依据）
        st = json.loads(Path(self.state).read_text())
        self.assertEqual(st["sessionId"], SID)
        self.assertEqual(st["source"], "prompt-submit")
        self.assertGreaterEqual(st["promptTs"], before)

    def test_hook_state_then_guard_blocks_stale_turn(self):
        # 端到端：hook 刚记录完提问（此刻本问必然无入库数据）→ --current 必须无输出
        self.run_hook()
        r = self.run_rate("--turn", "--current", state=self.state)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "")

    def test_hook_without_session_env_still_valid_json(self):
        env = dict(os.environ, ZCODE_USAGE_DB=self.db, ZUSAGE_RATE_STATE_FILE=self.state,
                   ZUSAGE_PLUGIN_RUNTIME=str(self.runtime))
        env.pop("ZCODE_SESSION_ID", None)
        env.pop("CLAUDE_SESSION_ID", None)
        r = subprocess.run([sys.executable, str(PROMPT_HOOK)], input="",
                           capture_output=True, text=True, env=env, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "UserPromptSubmit")
        self.assertFalse(Path(self.state).exists())  # 无会话不写状态

    def test_toggle_off_injects_no_instruction(self):
        # ⚙ 面板关闭（config.json answer_rate_line=false）：不下达统计指令，仍严格 JSON；
        # 状态文件照写（--current 守卫数据保持新鲜，CLI 手动排查不受开关影响）
        (self.runtime / "config.json").write_text(
            '{"answer_rate_line": false, "lang": "zh"}', encoding="utf-8")
        r = self.run_hook()
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual(set(out), {"hookSpecificOutput"})
        inner = out["hookSpecificOutput"]
        self.assertEqual(set(inner), {"hookEventName", "additionalContext"})
        self.assertEqual(inner["hookEventName"], "UserPromptSubmit")
        self.assertNotIn("本轮统计指令", inner["additionalContext"])
        self.assertNotIn("内部背景", inner["additionalContext"])
        st = json.loads(Path(self.state).read_text())
        self.assertEqual(st["sessionId"], SID)

    def test_toggle_corrupt_config_defaults_on(self):
        # config 损坏 → 视为开启（开关失效不吞功能）
        (self.runtime / "config.json").write_text("{broken", encoding="utf-8")
        r = self.run_hook()
        out = json.loads(r.stdout)
        self.assertIn("本轮统计指令", out["hookSpecificOutput"]["additionalContext"])

    def test_toggle_explicit_true(self):
        # 显式 true 与缺省同义：开启
        (self.runtime / "config.json").write_text('{"answer_rate_line": true}', encoding="utf-8")
        r = self.run_hook()
        out = json.loads(r.stdout)
        self.assertIn("本轮统计指令", out["hookSpecificOutput"]["additionalContext"])

    def test_state_written_has_per_session_map(self):
        # hook 写状态带 prompts 每会话映射（守卫按会话取提问时刻的依据）
        self.run_hook()
        st = json.loads(Path(self.state).read_text())
        self.assertEqual(st["prompts"][SID], st["promptTs"])
        # 再来一个会话提问：全局字段跟随新会话，映射里两个会话各自保留
        self.run_hook(sid="sess_second")
        st2 = json.loads(Path(self.state).read_text())
        self.assertEqual(st2["sessionId"], "sess_second")
        self.assertIn(SID, st2["prompts"])
        self.assertIn("sess_second", st2["prompts"])
        self.assertGreater(st2["prompts"]["sess_second"], st2["prompts"][SID])


if __name__ == "__main__":
    unittest.main(verbosity=2)
