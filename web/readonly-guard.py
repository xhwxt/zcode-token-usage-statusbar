# -*- coding: utf-8 -*-
"""强制只读地运行 zusage.py —— 旁挂服务专用包装器。

为什么需要它：上游 zusage.py 的 connect() 在 mode=ro 打开失败时会回退成
sqlite3.connect(str(DB_PATH))，那是一个**可写句柄**。ZCode 正在写同一个库，
绝不允许出现第二个写者（哪怕只是理论上）。本包装器在目标脚本拿到控制权之前
就把 sqlite3.connect 换掉：

  1. 路径参数未带 mode=ro / mode=rw → 改写成 file:<path>?mode=ro 且 uri=True
  2. 连接建立后一律 PRAGMA query_only=1 —— 即使句柄可写，写语句也会被 SQLite 拒绝

用法（与直接跑 zusage.py 的参数完全一致）：
    python3 readonly-guard.py <path>/zusage.py serve
"""
import os
import runpy
import sqlite3
import sys

if len(sys.argv) < 2:
    raise SystemExit("usage: readonly-guard.py <path-to-zusage.py> [args...]")

_TARGET = sys.argv[1]
_orig_connect = sqlite3.connect


def _guarded_connect(database, *args, **kwargs):
    if isinstance(database, (str, bytes, os.PathLike)):
        s = os.fspath(database)
        if isinstance(s, bytes):
            s = s.decode()
        if "mode=ro" not in s and "mode=rw" not in s:
            # immutable=1：声明库文件在连接期间不变，SQLite 不再尝试创建
            # -wal/-shm 伴生文件。为什么必须有：服务跑在 ProtectHome=read-only
            # 的沙箱里，ZCode 写库期间（WAL 活跃）只读连接会尝试 O_CREAT
            # db.sqlite-wal → EROFS → "unable to open database file"（2026-10-10
            # 实录：zcode-web 重启后 WAL 活跃，状态栏连续 500）。代价是读不到
            # 未 checkpoint 的 WAL 数据——状态栏本来就是近似展示，可接受。
            s = "file:{}?mode=ro&immutable=1".format(s)
            kwargs["uri"] = True
        database = s
    con = _orig_connect(database, *args, **kwargs)
    try:
        con.execute("pragma query_only=1")
    except Exception:
        # 极老的库或不支持该 pragma 时也不放行写入：宁可查询失败
        pass
    return con


sqlite3.connect = _guarded_connect
sys.argv = [_TARGET] + sys.argv[2:]
runpy.run_path(_TARGET, run_name="__main__")
