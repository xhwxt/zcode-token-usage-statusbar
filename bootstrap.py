# -*- coding: utf-8 -*-
"""一键部署 / 自检 / 修复（给"别人帮我装"这个场景用的）。

两种用法：

  1. 人跑（已经 clone 了仓库）：
       python bootstrap.py

  2. AI 跑（把下面这段链接发给任意一个能跑命令的 AI）：
       请阅读 https://raw.githubusercontent.com/suireng0219/zcode-token-usage-statusbar/perf/startup-lite/bootstrap.py
       并按其说明帮我安装。全程不要让我手动改文件。

参数：

  --check      只体检，不改任何东西（退出码 0=健康 / 1=需要修 / 2=环境缺失）
  --repair     只修注入（等价于 autopatch.py apply，静默）
  --install    完整安装（等价于 install.py，带自动定位重试）
  --uninstall  卸载
  --json       机器可读输出（给 AI 用；人类可读输出默认开启彩色/对白）
  --root PATH   ZCode 安装目录（自动探测失败时用）
  --dry-run    只打印计划

设计约束（重要，别改坏）：

  * 只用标准库，Python 3.8+，不下载任何东西、不联网。用户把仓库做成 zip
    传给 AI，AI 解压后直接跑这个脚本即可。
  * 所有"猜"的环节都必须能解释：探测失败时打印试过哪些路径，而不是静默失败。
  * 任何写操作前先 --check 一遍；写失败必须回滚/给出可执行的下一步。
  * 不写入仓库以外的位置（除了 ZCode 的 asar 和 ~/.zcode/ 下的数据目录）。
"""

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
IS_WIN = os.name == "nt"
PY = sys.executable or "python3"

# 运行时清单（与 install.py 的 RUNTIME_FILES 保持一致）
RUNTIME_FILES = ("inject-main.cjs", "overlay.js", "zusage.py", "usage_mcp.py",
                 "settings.html", "settings-preload.cjs")

EXIT_OK, EXIT_NEED_FIX, EXIT_ENV_MISSING = 0, 1, 2


# ---------------------------------------------------------------- 环境体检

def python_ok():
    v = sys.version_info
    return {"ok": v >= (3, 8), "version": "%d.%d.%d" % (v[0], v[1], v[2]),
            "path": PY, "min": "3.8"}


def files_ok():
    missing = [n for n in RUNTIME_FILES if not (HERE / n).is_file()]
    return {"ok": not missing, "missing": missing, "dir": str(HERE)}


def zcode_roots():
    """按平台给候选根目录。与 install.py 的探测保持一致，但这里只用于报告。"""
    home = Path.home()
    if sys.platform == "darwin":
        return [Path("/Applications/ZCode.app"), home / "Applications/ZCode.app"]
    if sys.platform.startswith("linux"):
        return [Path("/opt/ZCode"), Path("/opt/zcode"),
                Path("/usr/lib/zcode"), Path("/usr/share/zcode"),
                home / ".local/share/ZCode"]
    if IS_WIN:
        out = []
        # 用户级安装：<LOCALAPPDATA>\Programs\ZCode 与 <LOCALAPPDATA>\ZCode
        local = os.environ.get("LOCALAPPDATA")
        if local:
            out += [Path(local) / "Programs" / "ZCode", Path(local) / "ZCode"]
        # 机器级安装：Program Files 下没有 "Programs" 这一层
        for env in ("PROGRAMFILES", "PROGRAMFILES(X86)"):
            base = os.environ.get(env)
            if base:
                out.append(Path(base) / "ZCode")
        out.append(Path("C:/Program Files/ZCode"))
        return out
    return []


def find_asar():
    """返回 (路径, 来源说明)。来源说明要能让 AI 向用户解释。"""
    env = os.environ.get("ZCODE_ASAR")
    if env and Path(env).is_file():
        return Path(env), "环境变量 ZCODE_ASAR"
    # 运行时配置里记住的位置（install.py 首次成功后写入）
    for cand in (Path.home() / ".zcode" / "zcode-token-usage-statusbar" / "config.json",
                 HERE / "config.json"):
        try:
            cfg = json.loads(cand.read_text(encoding="utf-8"))
            p = cfg.get("asar_path")
            if p and Path(p).is_file():
                return Path(p), "config.json 记录的 asar_path"
        except (OSError, ValueError):
            pass
    tried = []
    for root in zcode_roots():
        if sys.platform == "darwin" and root.name.endswith(".app"):
            asar = root / "Contents" / "Resources" / "app.asar"
        else:
            asar = root / "resources" / "app.asar"
        tried.append(str(asar))
        if asar.is_file():
            return asar, "平台默认目录"
    return None, "未找到；试过: " + (" | ".join(tried) if tried else "(无候选)")


def zcode_identity(asar):
    """确认目标是 ZCode，别把注入写进别的 Electron 应用（issue #6 的教训）。"""
    if not asar:
        return {"ok": False, "reason": "没有 asar 路径"}
    exe = asar.parent.parent / ("ZCode.exe" if IS_WIN else "ZCode")
    if exe.is_file():
        return {"ok": True, "reason": "同级存在 " + exe.name}
    try:
        # 头部 JSON 里 package.json 的 name 含 zcode 也算
        import struct
        with open(asar, "rb") as f:
            head = f.read(16)
            if len(head) < 16:
                return {"ok": False, "reason": "asar 头部过短"}
            size = struct.unpack("<I", head[12:16])[0]
            blob = f.read(size)
        if b'"name"' in blob and b"zcode" in blob.lower():
            return {"ok": True, "reason": "asar 内 package.json 的 name 含 zcode"}
    except Exception as e:
        return {"ok": False, "reason": "读取 asar 头失败: %s" % e}
    return {"ok": False, "reason": "无法确认这是 ZCode（同级无 ZCode.exe，包名也不含 zcode）"}


def injection_state():
    """跑 autopatch check：注入行在不在。"""
    ap = HERE / "autopatch.py"
    if not ap.is_file():
        return {"ok": False, "state": "missing-autopatch", "detail": "缺少 autopatch.py"}
    try:
        p = subprocess.run([PY, str(ap), "check"], capture_output=True, timeout=120)
        out = (p.stdout + p.stderr).decode("utf-8", "replace").strip()
        return {"ok": p.returncode == 0, "state": p.returncode,
                "detail": out.splitlines()[-1] if out else ""}
    except subprocess.TimeoutExpired:
        return {"ok": False, "state": "timeout", "detail": "check 超时"}
    except OSError as e:
        return {"ok": False, "state": "error", "detail": str(e)}


def config_state():
    cfg = Path.home() / ".zcode" / "zcode-token-usage-statusbar" / "config.json"
    if not cfg.is_file():
        return {"ok": False, "present": False, "path": str(cfg)}
    try:
        data = json.loads(cfg.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return {"ok": False, "present": True, "path": str(cfg), "error": str(e)}
    pp = data.get("python_path")
    pp_ok = bool(pp) and Path(pp).is_file()
    return {"ok": pp_ok, "present": True, "path": str(cfg),
            "python_path": pp, "python_path_exists": pp_ok,
            "archive_mode": data.get("archive_mode"), "lang": data.get("lang")}


def mcp_state():
    """MCP 注册在 ~/.zcode/cli/config.json 的 mcp.servers 下。"""
    p = Path.home() / ".zcode" / "cli" / "config.json"
    if not p.is_file():
        return {"ok": False, "present": False, "path": str(p)}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return {"ok": False, "present": True, "error": str(e)}
    servers = ((data.get("mcp") or {}).get("servers") or {})
    hit = "zcode-token-usage-statusbar" in servers
    return {"ok": hit, "present": True, "registered": hit,
            "names": sorted(servers.keys())}


def check_all():
    asar, how = find_asar()
    ident = zcode_identity(asar) if asar else {"ok": False, "reason": how}
    # 找不到 asar 就别跑注入检查：autopatch 会回落到缓存位置或探测，
    # 得出的"未注入"是误报，会把"没装 ZCode"混淆成"注入丢了"。
    inj = injection_state() if asar else {
        "ok": False, "state": "unknown", "detail": "找不到 app.asar，跳过"}
    report = {
        "platform": {"os": platform.system(), "release": platform.release(),
                     "machine": platform.machine()},
        "python": python_ok(),
        "files": files_ok(),
        "asar": {"path": str(asar) if asar else None, "found_by": how,
                 "identity": ident},
        "injection": inj,
        "config": config_state(),
        "mcp": mcp_state(),
    }
    problems = []
    if not report["python"]["ok"]:
        problems.append("Python < 3.8，请升级（当前 %s）" % report["python"]["version"])
    if not report["files"]["ok"]:
        problems.append("仓库缺少文件: " + ", ".join(report["files"]["missing"]))
    if not asar:
        problems.append("找不到 ZCode 的 app.asar：" + how)
    elif not ident.get("ok"):
        problems.append("无法确认目标是 ZCode：" + ident.get("reason", ""))
    if report["injection"]["state"] != "unknown" and not report["injection"]["ok"]:
        problems.append("状态条注入缺失或被 ZCode 升级覆盖（--repair 可修）")
    if not report["config"]["ok"]:
        if not report["config"].get("present"):
            problems.append("尚未安装（没有 config.json）")
        elif not report["config"].get("python_path_exists"):
            problems.append("config.json 里的 python_path 指向的解释器不存在")
        else:
            problems.append("config.json 有问题: " + str(report["config"].get("error")))
    if not report["mcp"]["ok"]:
        problems.append("MCP 未注册（可选，只影响对话内查询）")
    report["problems"] = problems
    report["healthy"] = not problems
    if not report["python"]["ok"] or not asar:
        report["exit"] = EXIT_ENV_MISSING
    elif problems:
        report["exit"] = EXIT_NEED_FIX
    else:
        report["exit"] = EXIT_OK
    return report


# ---------------------------------------------------------------- 动作

def run_script(name, extra=None, timeout=900):
    script = HERE / name
    if not script.is_file():
        return {"ok": False, "error": "缺少 %s" % name}
    cmd = [PY, str(script)] + (extra or [])
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout)
        return {"ok": p.returncode == 0, "rc": p.returncode,
                "stdout": p.stdout.decode("utf-8", "replace")[-4000:],
                "stderr": p.stderr.decode("utf-8", "replace")[-2000:]}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "%s 超时（%ds）—— 重打包大 asar 时可能较慢" % (name, timeout)}
    except OSError as e:
        return {"ok": False, "error": str(e)}


def do_repair(dry):
    if dry:
        return {"ok": True, "dry_run": True, "would": "autopatch.py apply"}
    return run_script("autopatch.py", ["apply"])


def do_install(dry, root=None):
    extra = []
    if root:
        extra += ["--root", str(root)]
    if dry:
        extra.append("--dry-run")
    return run_script("install.py", extra)


def do_uninstall(dry):
    extra = ["--remove"] + (["--dry-run"] if dry else [])
    return run_script("install.py", extra)


# ---------------------------------------------------------------- 输出

def emit(report, as_json):
    if as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    p = report.get("platform", {})
    print("=" * 62)
    print(" ZCode Token 用量状态栏 · 部署体检")
    print("=" * 62)
    print(" 平台      : %s %s (%s)" % (p.get("os"), p.get("release"), p.get("machine")))
    py = report.get("python", {})
    print(" Python    : %s %s" % (py.get("version"), "OK" if py.get("ok") else "版本过低"))
    f = report.get("files", {})
    print(" 仓库文件  : %s" % ("齐备" if f.get("ok") else "缺 " + ", ".join(f.get("missing", []))))
    a = report.get("asar", {})
    print(" app.asar  : %s" % (a.get("path") or "未找到"))
    print("             (来源: %s)" % a.get("found_by"))
    ident = a.get("identity") or {}
    print(" 身份确认  : %s — %s" % ("通过" if ident.get("ok") else "失败", ident.get("reason", "")))
    inj = report.get("injection", {})
    print(" 注入状态  : %s %s" % ({0: "已注入", 1: "缺失/被覆盖", 2: "环境问题"}
                                  .get(inj.get("state"), inj.get("state")), inj.get("detail", "")))
    c = report.get("config", {})
    print(" 配置      : %s" % (c.get("path") if c.get("present") else "未安装"))
    m = report.get("mcp", {})
    print(" MCP       : %s" % ("已注册" if m.get("ok") else "未注册"))
    print("-" * 62)
    problems = report.get("problems") or []
    if not problems:
        print(" 结论：健康，不需要做任何事。")
    else:
        print(" 结论：发现 %d 个问题" % len(problems))
        for i, x in enumerate(problems, 1):
            print("   %d. %s" % (i, x))
        print(" 修复：python bootstrap.py --repair     （只补注入，不碰别的）")
        print("       python bootstrap.py --install     （完整安装/重装）")
    print("=" * 62)


def main(argv):
    ap = argparse.ArgumentParser(
        add_help=True,
        description="ZCode Token 用量状态栏：一键部署 / 体检 / 修复")
    ap.add_argument("--check", action="store_true", help="只体检，不改任何东西")
    ap.add_argument("--repair", action="store_true", help="只补注入（autopatch apply）")
    ap.add_argument("--install", action="store_true", help="完整安装（install.py）")
    ap.add_argument("--uninstall", action="store_true", help="卸载")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    ap.add_argument("--root", help="ZCode 安装目录（自动探测失败时用）")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划")
    args = ap.parse_args(argv)

    # 无参数 = 体检 + 按需修复（最省心的默认行为）
    if not any([args.check, args.repair, args.install, args.uninstall]):
        report = check_all()
        if report["exit"] == EXIT_OK:
            emit(report, args.json)
            return EXIT_OK
        if report["exit"] == EXIT_ENV_MISSING:
            emit(report, args.json)
            return EXIT_ENV_MISSING
        # 环境齐、只是缺注入/配置：继续走完整安装
        print("[自动] 体检发现问题，开始安装…" if not args.json else "", file=sys.stderr)
        res = do_install(args.dry_run, args.root)
        report["action"] = {"install": res}
        report = check_all()
        report["action"] = {"install": res}
        emit(report, args.json)
        return report["exit"]

    if args.check:
        report = check_all()
        emit(report, args.json)
        return report["exit"]

    if args.repair:
        res = do_repair(args.dry_run)
        report = check_all()
        report["action"] = {"repair": res}
        emit(report, args.json)
        return EXIT_OK if res.get("ok") else EXIT_NEED_FIX

    if args.install:
        res = do_install(args.dry_run, args.root)
        report = check_all()
        report["action"] = {"install": res}
        emit(report, args.json)
        return EXIT_OK if res.get("ok") else EXIT_NEED_FIX

    if args.uninstall:
        res = do_uninstall(args.dry_run)
        report = check_all()
        report["action"] = {"uninstall": res}
        emit(report, args.json)
        return EXIT_OK if res.get("ok") else EXIT_NEED_FIX

    return EXIT_OK


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        print("\n已中断", file=sys.stderr)
        sys.exit(130)
