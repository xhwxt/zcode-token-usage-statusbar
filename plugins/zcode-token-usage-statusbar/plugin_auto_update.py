# -*- coding: utf-8 -*-
"""SessionStart 的独立自动更新 bootstrap；不安装或恢复 asar 注入。"""
import json
import os
from pathlib import Path
import subprocess
import sys
import time


INTERVAL_MS = 24 * 60 * 60 * 1000


def start_auto_update(runtime, pi):
    """默认后台检查更新，保留显式关闭；最终限频与跨进程锁由 Node 负责。"""
    try:
        runtime = Path(runtime)
        cfg = json.loads((runtime / "config.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        cfg = {}
    except (OSError, ValueError, TypeError):
        return False
    if not isinstance(cfg, dict) or cfg.get("plugin_auto_update", True) is not True:
        return False

    # 远程会话或本机 asar 不可读时保持静默，不产生安装/更新提示。
    try:
        asar = Path(pi.ASAR)
        with asar.open("rb") as stream:
            stream.read(1)
    except (OSError, ValueError, TypeError, AttributeError):
        return False

    try:
        try:
            state = json.loads((runtime / "plugin-update.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            state = {}
        if isinstance(state, dict) and state.get("pendingSync") is not True:
            last_attempt = state.get("lastAttempt")
            # Node 的 Date.now() 以毫秒记录；损坏字段不阻止新的尝试。
            elapsed = time.time() * 1000 - last_attempt if type(last_attempt) in (int, float) else None
            if (elapsed is not None and 0 < last_attempt < float("inf")
                    and 0 <= elapsed < INTERVAL_MS):
                return False

        executable = getattr(pi, "ZCODE_EXE", None)
        if not executable or not Path(executable).is_file():
            executable = pi.zcode_exe_for(asar)
        if (not executable or not Path(executable).is_file()
                or (sys.platform != "win32" and not os.access(executable, os.X_OK))):
            print("[zusage-plugin] 自动更新未启动：找不到有效的 ZCode 可执行文件。", file=sys.stderr)
            return False
        bundle = asar.parent / "glm" / "zcode.cjs"
        if not bundle.is_file():
            print(f"[zusage-plugin] 自动更新未启动：ZCode CLI bundle 不存在：{bundle}", file=sys.stderr)
            return False
        loader = runtime / "plugin-updater.cjs"
        if not loader.is_file():
            print(f"[zusage-plugin] 自动更新未启动：更新器不存在：{loader}", file=sys.stderr)
            return False

        env = os.environ.copy()
        env["ELECTRON_RUN_AS_NODE"] = "1"
        env["ZUSAGE_PLUGIN_PYTHON"] = sys.executable
        detach = ({"creationflags": subprocess.DETACHED_PROCESS
                   | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW}
                  if sys.platform == "win32" else {"start_new_session": True})
        subprocess.Popen(
            [str(executable), str(loader), "--auto", "--resources", str(asar.parent),
             "--runtime", str(runtime)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            cwd=str(Path.home()), env=env, shell=False, **detach)
        return True
    except Exception as e:  # noqa: BLE001 — 自动更新不能影响 SessionStart hook
        print(f"[zusage-plugin] 自动更新启动失败（已忽略）：{e}", file=sys.stderr)
        return False
