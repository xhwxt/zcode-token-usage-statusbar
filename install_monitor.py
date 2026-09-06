# -*- coding: utf-8 -*-
"""「ZCode Token 用量状态栏」安装监控窗口（安装完成后自动弹出的常驻命令行）。

每 10 秒检测一次，全程只读（唯一的写操作是 TMP 替换，见下），不联网：
1. 存在待替换 TMP（客户端运行中导致原子替换失败的场景）：等 ZCode 退出后自动完成
   asar 替换 —— 取代旧的 schtasks 计划任务收尾方案（finalize_if_closed.py 已删）。
2. asar 已替换：持续提醒重启 ZCode；检测到 ZCode 换了新进程（进程集合与启动采集的
   不相交）后，用 diag-<n>.json 的更新时间确认注入链路已加载，显示成功并自动退出。
   diag 是启动后第一次数据拉取就回写的（pushCount==1），空闲也会写，不依赖用户发消息。

手动运行：python install_monitor.py [install_epoch 秒] [app.asar 路径] [运行时目录]
（缺省用当前时间/默认 asar 位置；diag 在运行时目录——标准安装是数据目录副本所在，
 --dev 安装传仓库目录，不传则用本脚本所在目录）
退出：生效确认后自动退出；或 Ctrl+C / 直接关闭窗口。

输出语言：运行时目录 config.json 的 "lang" 字段（zh/en，缺省 zh）——与安装器/CLI 同一来源。
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent.resolve()
ASAR = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(r"D:\ZCode\resources\app.asar")
RUNTIME_DIR = Path(sys.argv[3]) if len(sys.argv) > 3 else HERE
TMP = ASAR.with_name("app.asar.zusage.tmp")
POLL = 10            # 检测间隔（秒）
CONFIRM_WAIT = 120   # 重启后等 diag 确认的超时（秒）
BYE = 8              # 成功提示停留秒数


def load_lang():
    try:
        lang = json.loads((RUNTIME_DIR / "config.json").read_text(encoding="utf-8")).get("lang")
        return lang if lang in ("zh", "en") else "zh"
    except (OSError, ValueError):
        return "zh"


LANG = load_lang()


def L(zh, en):
    """双语输出：按 LANG 返回对应文案（与 install.py 同款约定）。"""
    return en if LANG == "en" else zh


def zcode_pids():
    """ZCode.exe 全部进程的 PID 集合；tasklist 不可用时返回 None。"""
    try:
        r = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq ZCode.exe", "/FO", "CSV", "/NH"],
            capture_output=True,
        )
    except OSError:
        return None
    pids = set()
    for line in r.stdout.decode("gbk", "replace").splitlines():
        parts = [p.strip().strip('"') for p in line.split('","')]
        if len(parts) >= 2 and parts[0].lower() == "zcode.exe":
            pids.add(parts[1])
    return pids


def diag_fresh(since):
    """返回安装时刻之后更新过的 diag 文件名（注入链路已加载的证据），没有则 None。"""
    for f in sorted(RUNTIME_DIR.glob("diag-*.json")):
        try:
            if f.stat().st_mtime >= since:
                return f.name
        except OSError:
            pass
    return None


def finalize_tmp():
    try:
        os.replace(TMP, ASAR)
        return True
    except OSError:
        return False


def draw(lines):
    os.system("cls" if os.name == "nt" else "clear")
    print("\n".join(lines), flush=True)


def main():
    epoch = float(sys.argv[1]) if len(sys.argv) > 1 else time.time()
    if os.name == "nt":
        os.system("title " + L("ZCode Token 用量状态栏 - 安装监控", "ZCode Token Usage Status Bar - Install Monitor"))
    pids0 = zcode_pids() or set()   # 启动采集 ≈ 安装时刻的进程集合（install 完成即拉起本窗口）
    t0 = time.time()
    new_since = None
    while True:
        pids = zcode_pids()
        now = time.time()
        done = False
        if pids is None:
            lines = [L("[!] 无法检测 ZCode 进程（tasklist 不可用）。",
                       "[!] Cannot detect ZCode processes (tasklist unavailable).")]
        elif TMP.exists():
            # 运行中替换失败的收尾：ZCode 一退出就自动替换，无需计划任务
            if not pids:
                if finalize_tmp():
                    lines = [L("[OK] ZCode 已退出，asar 替换完成。", "[OK] ZCode has exited; app.asar replacement finished."),
                             "",
                             L("请启动 ZCode，悬浮条即可显示。", "Start ZCode and the floating bar will appear.")]
                else:
                    lines = [L("[!] ZCode 已退出但替换失败（文件仍被占用？）",
                               "[!] ZCode has exited but the replacement failed (file still locked?)"),
                             L("稍后自动重试；也可手动执行：", "Will retry automatically; you can also run manually:"),
                             "  python patch_install.py install --finalize"]
            else:
                lines = [L("[等待] 有待替换文件，请完全退出 ZCode。",
                           "[waiting] A pending replacement file exists; please exit ZCode completely."),
                         L("退出后本窗口将自动完成 asar 替换。",
                           "This window will finish the asar replacement automatically once you exit."),
                         "", L("当前 ZCode 进程数：%d" % len(pids), "Current ZCode process count: %d" % len(pids))]
        elif not pids:
            lines = [L("ZCode 未运行。启动 ZCode 后悬浮条即生效。",
                       "ZCode is not running. The floating bar activates once you start ZCode.")]
        elif pids & pids0:
            lines = [L(">>>>>  请 重 启 Z Code  <<<<<", ">>>>>  PLEASE RESTART ZCODE  <<<<<"),
                     "",
                     L("安装已完成，但当前还是旧实例（%d 个进程），" % len(pids),
                       "Installation finished, but the old instance is still running (%d processes)," % len(pids)),
                     L("请完全退出 ZCode（所有窗口）后重新打开。",
                       "please quit ZCode completely (all windows) and reopen it.")]
        else:
            if new_since is None:
                new_since = now
            d = diag_fresh(epoch)
            if d:
                lines = [L("[OK] ZCode 已重启，注入已加载（%s 已更新）。" % d,
                           "[OK] ZCode restarted, injection loaded (%s updated)." % d),
                         L("悬浮条应已出现在输入框下方。", "The floating bar should now be visible."),
                         "", L("本窗口即将自动关闭。", "This window will close automatically.")]
                done = True
            elif now - new_since > CONFIRM_WAIT:
                lines = [L("[?] ZCode 已重启，但 %d 秒内未检测到注入加载。" % CONFIRM_WAIT,
                           "[?] ZCode restarted, but no injection load detected within %ds." % CONFIRM_WAIT),
                         L("若 ZCode 刚升级过，asar 会被官方版本覆盖，",
                           "If ZCode was just upgraded, app.asar was overwritten by the official build —"),
                         L("请重新运行：python patch_install.py install",
                           "please re-run: python patch_install.py install"),
                         L("（继续检测中，diag 一更新即确认）", "(still watching; will confirm as soon as diag updates)")]
            else:
                lines = [L("ZCode 已重启（新实例），正在确认注入加载……",
                           "ZCode restarted (new instance); confirming injection load…")]
        el = int(now - t0)
        lines += ["", "-" * 52,
                  L("已等待 %02d:%02d | 每 %d 秒检测一次 | 生效后自动退出" % (el // 60, el % 60, POLL),
                    "waited %02d:%02d | checking every %ds | exits automatically once confirmed" % (el // 60, el % 60, POLL)),
                  L("本窗口可随时关闭，不影响安装结果。", "You can close this window at any time; the installation result is unaffected.")]
        draw(lines)
        if done:
            time.sleep(BYE)
            return
        time.sleep(POLL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
