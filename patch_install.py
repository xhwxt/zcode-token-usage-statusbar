# -*- coding: utf-8 -*-
"""ZCode app.asar 注入/卸载工具（token 用量悬浮条）。

用法：
  python patch_install.py install   # 安装/重定向（幂等；自动替换旧注入行，含指向旧目录的历史行；
                                    # 客户端运行中则生成 .tmp 待退出后替换）
  python patch_install.py install --finalize  # 客户端退出后完成替换
  python patch_install.py remove    # 卸载（从 asar 剥离本工具注入行，不依赖备份）
  python patch_install.py check     # 检查当前注入状态与入口语法

原理：asar 主入口 out/main/index.js 尾部追加一行 dynamic import(loader)（ESM 入口，实测见下）。
Electron fuses: EmbeddedAsarIntegrityValidation=0（已实测），改动 asar 可正常加载。

安装/卸载均只增删本工具自己的注入行（ZUSAGE_LINE_RE 识别，不限目录的历史行一并匹配），
不制作也不依赖 asar 备份：卸载不会连带移除其它工具后装的注入行；ZCode 官方升级覆盖 asar
之后再卸载，也不会把客户端降级回安装时的旧版本。历史版本遗留的 app.asar.zusage.bak
会在成功安装/卸载后自动清理。

安装成功后自动弹出常驻监控窗口（install_monitor.py，每 10 秒检测一次）：提醒重启
ZCode、确认注入加载后自动退出；运行中替换失败（.tmp 待替换）时还会在 ZCode 退出后
自动完成替换 —— 该收尾职责已由监控窗口承担，旧的 schtasks 计划任务方案废弃。
"""
import hashlib
import json
import os
import re
import struct
import subprocess
import sys
import time
from pathlib import Path

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

if IS_WIN:
    ASAR_DEFAULT = Path(r"D:\ZCode\resources\app.asar")
    ZCODE_EXE_DEFAULT = Path(r"D:\ZCode\ZCode.exe")
elif IS_MAC:
    ASAR_DEFAULT = Path("/Applications/ZCode.app/Contents/Resources/app.asar")
    ZCODE_EXE_DEFAULT = Path("/Applications/ZCode.app/Contents/MacOS/ZCode")
else:
    ASAR_DEFAULT = Path("app.asar")   # 其它平台无默认位置，用 --asar / install.py 指定
    ZCODE_EXE_DEFAULT = None
ASAR = ASAR_DEFAULT          # 可被 set_target() 改指其它安装位置（install.py 探测后调用）
BAK = ASAR.with_name("app.asar.zusage.bak")
TMP = ASAR.with_name("app.asar.zusage.tmp")
HERE = Path(__file__).parent.resolve()
# 运行时目录：install.py 标准安装时指向数据目录（~/.zcode/zcode-token-usage-statusbar，
# 经 set_runtime() 切换）；独立运行本脚本或 --dev 安装时保持仓库目录（向后兼容）。
RUNTIME = HERE
LOADER = RUNTIME / "inject-main.cjs"
ENTRY = "out/main/index.js"
# 入口是 ESM（package.json type:module，实测 2026-09-04）：必须用 dynamic import（CJS/ESM 通用），
# 指向 .cjs loader（在 type:module 包里仍按 CJS 加载，require 可用）；asar 内相对路径不可用，用绝对 file URL。
INJECT_LINE_TMPL = (
    '\n;import("{url}").then(() => null, (e) => console.error("[zusage] load failed", e));'
)
INJECT_LINE = INJECT_LINE_TMPL.format(url=(RUNTIME / "inject-main.cjs").as_uri())
# 匹配任何 zusage 注入行（不限当前目录）——迁移/重装/标准↔dev 互切时剥离旧行用
ZUSAGE_LINE_RE = re.compile(
    rb'\n;import\("[^"]*inject-main\.cjs"\)\.then\(\(\) => null, '
    rb'\(e\) => console\.error\("\[zusage\] load failed", e\)\);'
)
ZCODE_EXE = ZCODE_EXE_DEFAULT
ALIGN = 4
BLOCK = 4194304

# 输出语言：惰性读 config.json 的 "lang"（运行时目录优先，其次数据目录；缺省 zh）。
# install.py 标准安装流程里 set_runtime() 先于 install()，此时序下读到的就是本次安装写入的语言。
_LANG = None


def L(zh, en):
    global _LANG
    if _LANG is None:
        _LANG = "zh"
        # sudo 运行时（Linux 系统级安装）config 在真实用户的家目录，Path.home() 会指到 root
        sudo_home = None
        if sys.platform != "win32" and os.environ.get("SUDO_USER"):
            try:
                if os.geteuid() == 0:
                    import pwd
                    sudo_home = Path(pwd.getpwnam(os.environ["SUDO_USER"]).pw_dir)
            except (KeyError, OSError, AttributeError):
                pass
        bases = [RUNTIME,
                 Path.home() / ".zcode" / "zcode-token-usage-statusbar"]
        if sudo_home:
            bases.append(sudo_home / ".zcode" / "zcode-token-usage-statusbar")
        for base in bases:
            try:
                lang = json.loads((base / "config.json").read_text(encoding="utf-8")).get("lang")
                if lang in ("zh", "en"):
                    _LANG = lang
                    break
            except (OSError, ValueError):
                pass
    return en if _LANG == "en" else zh


def set_runtime(path):
    """改运行时目录（LOADER/INJECT_LINE 随动）。ZCode 端泵的全套路径都基于 __dirname，
    运行时副本目录即自洽运行，无需改运行时代码。"""
    global RUNTIME, LOADER, INJECT_LINE
    RUNTIME = Path(path)
    LOADER = RUNTIME / "inject-main.cjs"
    INJECT_LINE = INJECT_LINE_TMPL.format(url=LOADER.as_uri())


def asar_package_name(asar_path):
    """读 asar 内 package.json 的 name 字段，辅助识别"这是不是 ZCode 的 asar"；
    读不到（非 asar / 无 package.json / 条目 unpacked 外置）返回 None。"""
    try:
        with open(asar_path, "rb") as f:
            a, b, c, d = struct.unpack("<4I", f.read(16))
            if a != 4:
                return None
            header = json.loads(f.read(d))
        node = header.get("files", {}).get("package.json")
        if not isinstance(node, dict) or "size" not in node:
            return None
        with open(asar_path, "rb") as f:
            f.seek(8 + b + int(node["offset"]))
            data = f.read(int(node["size"]))
        if b"\x00" in data:   # 被 pickle 对齐补零 / 条目加密等异常，放弃识别
            return None
        return json.loads(data.decode("utf-8")).get("name")
    except (OSError, ValueError, KeyError, struct.error):
        return None


def is_zcode_app(asar_path):
    """该 asar 是否属于 ZCode：可执行文件在 <root>/ZCode.exe，或包里 name 含 zcode。
    用于兜底扫描时排除同机其它 Electron 应用——%LOCALAPPDATA%\\Programs 下可能有
    opencode 等应用，命中它们会注入错目标且真 ZCode 永不生效。"""
    asar_path = Path(asar_path)
    if zcode_exe_for(asar_path) is not None:
        return True
    name = asar_package_name(asar_path)
    return isinstance(name, str) and "zcode" in name.lower()


def zcode_exe_for(asar_path):
    """从 asar 位置推 ZCode 可执行文件（语法自检用）：Windows 布局 <root>/ZCode.exe，
    macOS bundle 布局 <root>/MacOS/ZCode，Linux 布局 <root>/zcode（deb/rpm 官方包，/opt/ZCode）；
    非 ZCode 布局（如 %LOCALAPPDATA%\\Programs 下其它 Electron 应用）返回 None——
    客户端进程名随之失去语义（客户端是别的应用），语法检查自动跳过。"""
    root = asar_path.parent.parent
    if IS_WIN:
        exe = root / "ZCode.exe"
        return exe if exe.is_file() else None
    if IS_MAC:
        return root / "MacOS" / "ZCode"
    if sys.platform.startswith("linux"):
        return root / "zcode"
    return None


def set_target(asar_path):
    """改指目标安装位置（BAK/TMP/ZCODE_EXE 随动）。ZCODE_EXE 只用于语法自检，缺了自动跳过。"""
    global ASAR, BAK, TMP, ZCODE_EXE
    asar_path = Path(asar_path)
    ASAR = asar_path
    BAK = asar_path.with_name("app.asar.zusage.bak")
    TMP = asar_path.with_name("app.asar.zusage.tmp")
    ZCODE_EXE = zcode_exe_for(asar_path)


def client_running():
    try:
        if IS_WIN:
            out = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq ZCode.exe"],
                capture_output=True, text=True, encoding="gbk", errors="replace",
            ).stdout
            return "ZCode.exe" in out
        # macOS/Linux：pgrep 精确匹配主进程名；误判"未运行"在 POSIX 上也无害
        # （运行中替换本就允许：rename 语义，旧进程继续读旧 inode）
        return subprocess.run(["pgrep", "-x", "ZCode"], capture_output=True).returncode == 0
    except Exception:
        return True  # 查不到时按在跑处理，走安全路径


def launch_monitor(epoch):
    """Windows 开新控制台窗口跑安装监控（提醒重启 + 确认生效 + .tmp 收尾）；
    macOS/Linux 无控制台概念，后台静默跑（收尾与自动退出照常）。设 ZUSAGE_NO_MONITOR=1 可禁用。"""
    if os.environ.get("ZUSAGE_NO_MONITOR"):
        return
    monitor = HERE / "install_monitor.py"
    if not monitor.exists():
        return
    cmd = [sys.executable, str(monitor), "%.3f" % epoch, str(ASAR), str(RUNTIME)]
    try:
        if IS_WIN:
            return subprocess.Popen(cmd, creationflags=subprocess.CREATE_NEW_CONSOLE, cwd=str(HERE))
        return subprocess.Popen(cmd, cwd=str(HERE),
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as e:
        print(L(f"（监控窗口启动失败，不影响安装：{e}）",
                f"(monitor window failed to launch; installation is unaffected: {e})"))
        return None


# ---------- asar 读写 ----------

def read_header(f):
    f.seek(0)
    a, b, c, d = struct.unpack("<4I", f.read(16))
    assert a == 4, f"unexpected pickle prefix {a}"
    header = json.loads(f.read(d))
    return header, 8 + b


def iter_files(node, path=""):
    """yield (path_in_asar, node_dict)。node_dict 为文件（含 size）。"""
    for name, ch in node.get("files", {}).items():
        p = f"{path}/{name}"
        if "files" in ch:
            yield from iter_files(ch, p)
        else:
            yield p, ch


def compute_integrity(data: bytes):
    return {
        "algorithm": "SHA256",
        "hash": hashlib.sha256(data).hexdigest(),
        "blockSize": BLOCK,
        "blocks": [hashlib.sha256(data[i:i + BLOCK]).hexdigest() for i in range(0, len(data), BLOCK)],
    }


def repack(src_path: Path, modify: dict, dst_path: Path):
    """重建 asar。modify: {asar内路径: 新内容bytes}。流式拷贝未改动文件。"""
    with open(src_path, "rb") as src:
        header, base = read_header(src)
        header = json.loads(json.dumps(header))  # 深拷贝
        nodes = dict(iter_files(header))
        missing = set(modify) - set(nodes)
        assert not missing, f"paths not in asar: {missing}"

        data_parts = []
        offset = 0
        for p, node in nodes.items():
            if node.get("unpacked"):
                node["offset"] = "0"
                continue
            if p in modify:
                data = modify[p]
                node["size"] = len(data)
                node["integrity"] = compute_integrity(data)
            else:
                src.seek(base + int(node["offset"]))
                data = src.read(node["size"])
            node["offset"] = str(offset)
            data_parts.append(data)
            offset += len(data)

        payload = b"".join(data_parts)
        header_bytes = json.dumps(header, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        d = len(header_bytes)
        c = (d + 4 + ALIGN - 1) // ALIGN * ALIGN
        b_total = c + 4
        with open(dst_path, "wb") as out:
            out.write(struct.pack("<4I", 4, b_total, c, d))
            out.write(header_bytes)
            out.write(b"\0" * (c - d - 4))  # pickle 对齐：数据区从 8+b = 12+c 处开始
            assert out.tell() == 8 + b_total, f"header pad mismatch: {out.tell()} vs {8 + b_total}"
            out.write(payload)
    return dst_path


def entry_bytes_of(asar_path: Path) -> bytes:
    with open(asar_path, "rb") as f:
        header, base = read_header(f)
        nodes = dict(iter_files(header))
        node = nodes["/" + ENTRY]
        f.seek(base + int(node["offset"]))
        return f.read(node["size"])


def self_check(asar_path: Path, expect=True):
    """结构自检：header 可解析、文件数、入口注入行符合预期（install 期望有，remove 期望无）。"""
    with open(asar_path, "rb") as f:
        header, base = read_header(f)
        n = sum(1 for _ in iter_files(header))
    has = INJECT_LINE.encode() in entry_bytes_of(asar_path)
    ok = has == expect
    print(L(f"  [check] header ok, 文件数={n}, 注入行{'符合' if ok else '不符合'}预期（期望{'有' if expect else '无'}）",
            f"  [check] header ok, files={n}, injection line {'as expected' if ok else 'UNEXPECTED'} (expected {'present' if expect else 'absent'})"))
    return ok


def syntax_check(asar_path: Path):
    """用 ZCode 自身当 node（ELECTRON_RUN_AS_NODE）对入口做语法检查；
    找不到/无法运行 ZCode 可执行文件时跳过（自检是锦上添花，不应挡安装）。"""
    exe = ZCODE_EXE
    if not exe or not exe.is_file():
        print(L("  [check] 未找到 ZCode 可执行文件，跳过语法检查",
                "  [check] ZCode executable not found; syntax check skipped"))
        return True
    src = entry_bytes_of(asar_path)
    tmp_js = HERE / ".entry-check.js"
    tmp_js.write_bytes(src)
    env = dict(os.environ, ELECTRON_RUN_AS_NODE="1")
    try:
        r = subprocess.run([str(exe), "--check", str(tmp_js)], capture_output=True, text=True, env=env)
    except OSError as e:
        r = None
        run_err = str(e)
    finally:
        tmp_js.unlink(missing_ok=True)
    if r is None:
        print(L(f"  [check] 语法检查无法执行（{run_err}），跳过",
                f"  [check] syntax check failed to run ({run_err}); skipped"))
        return True
    print(L("  [check] 语法检查 exit=", "  [check] node --check exit=") + str(r.returncode) + " " + r.stderr.strip()[:200])
    return r.returncode == 0


def cleanup_legacy_bak():
    """备份机制已退役（安装/卸载均改为剥离注入行）：清掉历史版本遗留的 .bak，省约 300MB 磁盘。"""
    if not BAK.exists():
        return
    try:
        BAK.unlink()
        print(L(f"已清理历史遗留备份 {BAK}", f"Removed legacy backup {BAK}"))
    except OSError as e:
        print(L(f"（遗留备份清理失败，可手动删除 {BAK}：{e}）",
                f"(failed to remove legacy backup {BAK}; delete it manually: {e})"))


# ---------- 安装 / 卸载 ----------

def install(finalize=False):
    """返回 True=已安装/已指向当前目录，False=失败或待收尾。"""
    assert LOADER.exists(), L(f"loader 缺失：{LOADER}", f"loader missing: {LOADER}")
    if not os.access(ASAR, os.W_OK) or not os.access(ASAR.parent, os.W_OK):
        print(L(f"[权限] app.asar 或所在目录不可写（Linux 上 /opt 等系统位置需要 root）：{ASAR}",
                f"[permission] app.asar or its directory is not writable (Linux system locations like /opt need root): {ASAR}"))
        print(L("请用 sudo 重跑（推荐走一键安装，会自动处理目标定位与数据目录归属）：",
                "Please re-run with sudo (the one-shot installer is recommended; it resolves the target and data-dir ownership automatically):"))
        print(L("  sudo python install.py            # 自动探测（含 /opt/ZCode）",
                "  sudo python install.py            # auto-detects (including /opt/ZCode)"))
        return False
    entry = entry_bytes_of(ASAR)
    stripped = ZUSAGE_LINE_RE.sub(b"", entry)   # 剥离任何旧注入行（含指向旧目录的）
    if stripped + INJECT_LINE.encode() == entry:
        print(L("已安装，注入行已指向当前目录。如需重装先 remove。",
                "Already installed; the injection line points at the current directory. Run remove first to reinstall."))
        cleanup_legacy_bak()
        return True

    new_entry = stripped + INJECT_LINE.encode()
    if stripped != entry:
        print(L("检测到旧注入行（可能指向旧目录），将替换为新路径。",
                "Old injection line detected (possibly pointing at an old directory); it will be replaced with the new path."))
    print(L("重打包（307MB，约需十几秒）...", "repacking (307MB, takes ~10-20 seconds)..."))
    repack(ASAR, {"/" + ENTRY: new_entry}, TMP)
    print(L("结构自检：", "self-check:"))
    if not (self_check(TMP) and syntax_check(TMP)):
        print(L("自检失败，未替换。TMP 保留供排查:", "self-check failed; not replaced. TMP kept for inspection:"), TMP)
        return False
    if client_running() and not finalize:
        try:
            os.replace(TMP, ASAR)
            print(L("\n客户端运行中，但 asar 原子替换成功（运行中进程仍读旧数据）。",
                    "\nZCode is running, but the asar was replaced atomically (running processes keep reading the old data)."))
            print(L("重启 ZCode 后悬浮条生效；已弹出监控窗口，确认生效后自动关闭。",
                    "The floating bar activates after restarting ZCode; a monitor window has opened and will close itself once the load is confirmed."))
            cleanup_legacy_bak()
            launch_monitor(time.time())
            return True
        except OSError as e:
            print(L(f"\n运行中替换失败（{e}）。已弹出监控窗口：完全退出 ZCode 后会自动完成替换，",
                    f"\nReplacement while running failed ({e}). A monitor window has opened: it finishes the replacement once ZCode quits completely,"))
            print(L("或之后手动执行：python patch_install.py install --finalize",
                    "or run manually afterwards: python patch_install.py install --finalize"))
            launch_monitor(time.time())
            return False
    os.replace(TMP, ASAR)
    print(L("完成。启动 ZCode 即可在窗口底部看到悬浮条。", "Done. Start ZCode and the floating bar appears at the bottom of the window."))
    cleanup_legacy_bak()
    launch_monitor(time.time())
    return True


def remove():
    """卸载：从当前 asar 剥离本工具注入行（不依赖备份，其它工具的注入行不受影响）。
    返回 True=已卸载/本就未注入，False=需人工处理（替换失败待收尾）。"""
    old = entry_bytes_of(ASAR)
    stripped = ZUSAGE_LINE_RE.sub(b"", old)
    if stripped == old:
        print(L("当前 asar 未注入。", "The current asar is not injected."))
        cleanup_legacy_bak()
        return True
    print(L("重打包（剥离注入行，约需十几秒）...", "repacking (stripping the injection line, takes ~10-20 seconds)..."))
    repack(ASAR, {"/" + ENTRY: stripped}, TMP)
    print(L("结构自检：", "self-check:"))
    if not (self_check(TMP, expect=False) and syntax_check(TMP)):
        print(L("自检失败，未替换。TMP 保留供排查:", "self-check failed; not replaced. TMP kept for inspection:"), TMP)
        return False
    running = client_running()
    try:
        os.replace(TMP, ASAR)
    except OSError as e:
        print(L(f"\n替换失败（{e}）。请完全退出 ZCode 后重跑 python patch_install.py remove，",
                f"\nReplacement failed ({e}). Quit ZCode completely and re-run python patch_install.py remove, "))
        hint = f"move /y {TMP} {ASAR}" if IS_WIN else f"mv -f '{TMP}' '{ASAR}'"
        print(L(f"或手动替换：{hint}", f"or replace manually: {hint}"))
        return False
    print(L("已剥离注入行" + ("（客户端运行中，已原子替换，运行中进程仍读旧数据）" if running else "") + "。重启 ZCode 后生效。",
            "Injection line stripped" + (" (ZCode was running; replaced atomically, the running process keeps the old data)" if running else "") + ". Takes effect after restarting ZCode."))
    cleanup_legacy_bak()
    return True


def check():
    injected = bool(ZUSAGE_LINE_RE.search(entry_bytes_of(ASAR)))   # 宽松匹配任意目录的历史注入行（独立运行时 INJECT_LINE 指向仓库路径，精确匹配会误报未注入）
    print("asar:", ASAR, ASAR.stat().st_size, "bytes")
    print(L("注入状态:", "Injection:"), L("已注入", "injected") if injected else L("未注入", "not injected"))
    print(L("遗留备份（机制已退役，成功安装/卸载后自动清理）:", "Legacy backup (retired; auto-removed on successful install/remove):"), BAK.exists())
    if injected:
        syntax_check(ASAR)
    if TMP.exists():
        print(L("存在待替换 TMP:", "Pending replacement TMP exists:"), TMP, L("（退出 ZCode 后跑 install --finalize）", "(quit ZCode, then run install --finalize)"))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    {"install": install, "remove": remove, "check": check}.get(cmd, check)()
