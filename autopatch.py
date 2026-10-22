# -*- coding: utf-8 -*-
"""ZCode 升级后自动重新注入（半自动「一劳永逸」方案）。

原理
----
官方升级会整包覆盖 `app.asar`，本工具的注入行随之消失。除此之外的东西都还在：
运行时目录（`~/.zcode/zcode-token-usage-statusbar/`）、MCP 注册、`/usage` 命令。

所以只需要在 ZCode **启动前**判断「asar 里还有没有我们的注入行」，没有就补上。
本脚本做这件事，并且只做这件事：

  python autopatch.py check     # 只检查，打印状态（退出码 0=已注入 / 1=需要补）
  python autopatch.py apply     # 需要时补注入（等价于 patch_install install 的幂等版）

为什么不改快捷方式
------------------
接管启动路径需要同时处理桌面/开始菜单/任务栏快捷方式、`zcode://` 协议处理器、
右键菜单动词，而且 electron-updater 在升级后会重建快捷方式，改动会丢。
`check` 更简单也更稳：校验一次 asar 头部（74~99ms），不满足才走重打包。

为什么不常驻
------------
常驻进程要一直占一份内存/句柄，而升级是低频事件。以「启动前跑一次」的钩子
（或手动执行）成本最低。

限制
----
- 本脚本**不负责**首次安装（运行时文件不存在时直接提示去跑 install.py）。
- asar 正在被运行时无法替换：会提示先完全退出 ZCode，或交给 install_monitor.py。
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).parent.resolve()
sys.path.insert(0, str(HERE))

import patch_install as P   # noqa: E402


def _lang():
    for base in (HERE / "config.json",
                 Path.home() / ".zcode" / "zcode-token-usage-statusbar" / "config.json"):
        try:
            lg = json.loads(base.read_text(encoding="utf-8")).get("lang")
            if lg in ("zh", "en"):
                return lg
        except (OSError, ValueError):
            pass
    return "zh"


LANG = _lang()


def L(zh, en):
    return en if LANG == "en" else zh


def runtime_dir():
    """运行时目录：优先标准数据目录（install.py 的落点），回退脚本所在目录（--dev）。"""
    std = Path.home() / ".zcode" / "zcode-token-usage-statusbar"
    if (std / "inject-main.cjs").exists():
        return std
    if (HERE / "inject-main.cjs").exists():
        return HERE
    return None


def find_target():
    """定位 asar：ZCODE_ASAR 环境变量 > 运行时 config 记住的路径 > 常见安装位置。
    探测面比 install.py 窄是刻意的——autopatch 只服务「已经装过一次」的场景，
    首次安装仍应走 install.py（它还会做 MCP 注册等）。"""
    import os

    env = os.environ.get("ZCODE_ASAR")
    if env and Path(env).is_file():
        return Path(env)

    rt = runtime_dir()
    if rt is not None:
        try:
            p = json.loads((rt / "config.json").read_text(encoding="utf-8")).get("asar_path")
            if p and Path(p).is_file():
                return Path(p)
        except (OSError, ValueError):
            pass

    cands = []
    if sys.platform == "darwin":
        cands = ["/Applications/ZCode.app/Contents/Resources/app.asar",
                 str(Path.home() / "Applications/ZCode.app/Contents/Resources/app.asar")]
    elif sys.platform.startswith("linux"):
        cands = ["/opt/ZCode/resources/app.asar", "/opt/zcode/resources/app.asar",
                 "/usr/lib/zcode/resources/app.asar", "/usr/local/ZCode/resources/app.asar"]
    else:
        la = os.environ.get("LOCALAPPDATA", "")
        pf = os.environ.get("ProgramFiles", r"C:\Program Files")
        cands = [r"D:\ZCode\resources\app.asar", r"C:\ZCode\resources\app.asar",
                 str(Path(la) / "Programs" / "ZCode" / "resources" / "app.asar") if la else "",
                 str(Path(la) / "ZCode" / "resources" / "app.asar") if la else "",
                 str(Path(pf) / "ZCode" / "resources" / "app.asar")]
    for c in cands:
        if c and Path(c).is_file():
            return Path(c)
    return None


def injected(asar: Path) -> bool:
    """只看 asar 入口尾部有没有本工具的注入行。只解析头部，不重打包。"""
    try:
        entry = P.entry_bytes_of(asar)
    except Exception:
        return False
    rt = runtime_dir()
    if rt is not None:
        want = P.INJECT_LINE_TMPL.format(url=(rt / "inject-main.cjs").as_uri()).encode()
        if want in entry:
            return True
    # 指向别的目录也算已注入（例如用户换过运行时位置），交给 install 去纠正
    return bool(P.ZUSAGE_LINE_RE.search(entry))


def main(argv):
    cmd = (argv[0] if argv else "check").lower()
    if cmd not in ("check", "apply"):
        print(L("用法: python autopatch.py [check|apply]",
                "usage: python autopatch.py [check|apply]"))
        return 2

    rt = runtime_dir()
    if rt is None:
        print(L("[!] 未找到运行时文件（inject-main.cjs）。请先运行：python install.py",
                "[!] Runtime files not found (inject-main.cjs). Run: python install.py"))
        return 2

    asar = find_target()
    if asar is None or not Path(asar).exists():
        print(L("[!] 未找到 app.asar。请先运行：python install.py --root <ZCode 安装目录>",
                "[!] app.asar not found. Run: python install.py --root <ZCode install dir>"))
        return 2

    asar = Path(asar)
    if injected(asar):
        print(L("[OK] 注入仍在：%s" % asar, "[OK] Injection present: %s" % asar))
        return 0

    print(L("[需要补注入] asar 已被官方升级覆盖：%s" % asar,
            "[needs patch] asar was overwritten by an official update: %s" % asar))
    if cmd == "check":
        print(L("执行：python autopatch.py apply", "Run: python autopatch.py apply"))
        return 1

    P.set_runtime(rt)
    P.set_target(asar)
    try:
        # monitor=False：自动场景不弹监控窗口（首次安装才需要，那走 install.py）
        P.install(monitor=False)
    except SystemExit as e:
        return e.code or 1
    ok = injected(asar)
    if ok:
        print(L("[OK] 已重新注入，重启 ZCode 生效。",
                "[OK] Re-injected; restart ZCode to take effect."))
        return 0
    print(L("[!] 补注入未完成（asar 可能仍被占用）。完全退出 ZCode 后重试。",
            "[!] Patch incomplete (asar may still be locked). Fully exit ZCode and retry."))
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
