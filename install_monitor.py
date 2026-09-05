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
"""
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
        os.system("title ZCode Token 用量状态栏 - 安装监控")
    pids0 = zcode_pids() or set()   # 启动采集 ≈ 安装时刻的进程集合（install 完成即拉起本窗口）
    t0 = time.time()
    new_since = None
    while True:
        pids = zcode_pids()
        now = time.time()
        done = False
        if pids is None:
            lines = ["[!] 无法检测 ZCode 进程（tasklist 不可用）。"]
        elif TMP.exists():
            # 运行中替换失败的收尾：ZCode 一退出就自动替换，无需计划任务
            if not pids:
                if finalize_tmp():
                    lines = ["[OK] ZCode 已退出，asar 替换完成。", "",
                             "请启动 ZCode，悬浮条即可显示。"]
                else:
                    lines = ["[!] ZCode 已退出但替换失败（文件仍被占用？）",
                             "稍后自动重试；也可手动执行：",
                             "  python patch_install.py install --finalize"]
            else:
                lines = ["[等待] 有待替换文件，请完全退出 ZCode。",
                         "退出后本窗口将自动完成 asar 替换。",
                         "", "当前 ZCode 进程数：%d" % len(pids)]
        elif not pids:
            lines = ["ZCode 未运行。启动 ZCode 后悬浮条即生效。"]
        elif pids & pids0:
            lines = [">>>>>  请 重 启 Z Code  <<<<<",
                     "",
                     "安装已完成，但当前还是旧实例（%d 个进程），" % len(pids),
                     "请完全退出 ZCode（所有窗口）后重新打开。"]
        else:
            if new_since is None:
                new_since = now
            d = diag_fresh(epoch)
            if d:
                lines = ["[OK] ZCode 已重启，注入已加载（%s 已更新）。" % d,
                         "悬浮条应已出现在输入框下方。",
                         "", "本窗口即将自动关闭。"]
                done = True
            elif now - new_since > CONFIRM_WAIT:
                lines = ["[?] ZCode 已重启，但 %d 秒内未检测到注入加载。" % CONFIRM_WAIT,
                         "若 ZCode 刚升级过，asar 会被官方版本覆盖，",
                         "请重新运行：python patch_install.py install",
                         "（继续检测中，diag 一更新即确认）"]
            else:
                lines = ["ZCode 已重启（新实例），正在确认注入加载……"]
        el = int(now - t0)
        lines += ["", "-" * 52,
                  "已等待 %02d:%02d | 每 %d 秒检测一次 | 生效后自动退出" % (el // 60, el % 60, POLL),
                  "本窗口可随时关闭，不影响安装结果。"]
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
