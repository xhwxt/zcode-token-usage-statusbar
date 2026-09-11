# -*- coding: utf-8 -*-
"""「ZCode Token 用量状态栏」一键安装（标准库实现，零依赖）。

标准安装形态：仓库只是源码，install.py 把运行时复制到数据目录
  ~/.zcode/zcode-token-usage-statusbar/
（inject-main.cjs / overlay.js / zusage.py / usage_mcp.py + config.json + diag 诊断产物），
asar 注入行与 MCP 注册都指向数据目录 —— 之后 clone 目录可以随意搬走或删除，
已安装实例照常运行，不存在"注入行指向已删除路径"的卸载残留。
（运行时全套基于 __dirname/__file__ 自定位，副本目录即自洽运行。）

用法（仓库根目录）：
  python install.py               # 全量安装：复制运行时 → 注入 asar → 注册 MCP → /usage 命令 → 监控窗口
  python install.py --asar PATH   # 指定 app.asar（自动探测失败时用；首次成功后记住，之后免传）
  python install.py --no-mcp      # 只装状态条，不动 MCP 与 /usage 命令
  python install.py --dev         # 开发模式：不复制运行时，注入直指本仓库目录，配置/诊断留在仓库
                                  #   （作者迭代用，改仓库文件即时热更新；与标准形态重跑 install 即互切）
  python install.py --remove      # 卸载：剥离 asar 注入行 + 移除 MCP 注册与命令 + 删除数据目录
  python install.py --dry-run     # 打印将执行的动作，不写任何文件
  python install.py --lang en     # 安装器与各组件的输出语言（zh/en；写入 config.json 供 CLI/MCP 沿用）

升级：git pull 后重跑 python install.py —— 注入行不变则 asar 不重打包（秒级），
overlay 副本刷新后由泵 2 秒内热重载；改了 inject-main.cjs（泵）才需要重启 ZCode。

ZCode 安装位置自动探测：环境变量 ZCODE_ASAR → 当前平台常见安装位置下找 resources\\app.asar
（Windows：D:\\ZCode 等；macOS：/Applications、~/Applications 下的 ZCode.app；
Linux：/opt/ZCode、/usr/lib/zcode 等 deb/rpm 布局）；
失败且终端可交互时询问，或用 --asar 指定。非默认位置首次安装成功后路径记住在 config.json
（asar_path 字段），之后的安装/卸载一律免传 --asar。
"""
import argparse
import json
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).parent.resolve()


def _sudo_user():
    """sudo 运行时的真实用户名（POSIX；非 sudo / Windows 返回 None）。"""
    if sys.platform != "win32" and os.environ.get("SUDO_USER"):
        try:
            if os.geteuid() == 0:
                return os.environ["SUDO_USER"]
        except AttributeError:
            pass
    return None


def real_home():
    """真实用户家目录：sudo 运行时 Path.home() 是 root 的家，而数据目录/MCP 注册/
    /usage 命令必须落在真实用户的 ~/.zcode（ZCode 客户端与泵都以普通用户身份运行）。
    Linux 上 /opt 等系统安装位置写入需要 root，这是本函数存在的唯一场景。"""
    sudo_user = _sudo_user()
    if sudo_user:
        try:
            import pwd
            return Path(pwd.getpwnam(sudo_user).pw_dir)
        except (KeyError, OSError):
            pass
    return Path.home()


DATA_DIR = real_home() / ".zcode" / "zcode-token-usage-statusbar"   # 标准安装的数据/运行目录
ZCODE_CONFIG = real_home() / ".zcode" / "cli" / "config.json"
COMMANDS_DIR = real_home() / ".zcode" / "commands"
MCP_NAME = "zcode-token-usage-statusbar"   # MCP server 注册名（与仓库名一致；老安装叫 token-usage/zusage，自动迁移）
MCP_NAME_OLD = ("token-usage", "zusage")   # 历史注册名
RUNTIME_FILES = ("inject-main.cjs", "overlay.js", "zusage.py", "usage_mcp.py")

# ZCode 安装位置候选：resources/app.asar 存在即命中（按序探测，按当前平台取组）
if sys.platform == "darwin":
    ASAR_CANDIDATES = [
        "/Applications/ZCode.app/Contents/Resources/app.asar",
        "~/Applications/ZCode.app/Contents/Resources/app.asar",
    ]
elif sys.platform.startswith("linux"):
    ASAR_CANDIDATES = [
        "/opt/ZCode/resources/app.asar",       # deb/rpm 官方包默认布局（<root>/zcode）
        "/opt/zcode/resources/app.asar",
        "/usr/lib/zcode/resources/app.asar",
        "/usr/local/ZCode/resources/app.asar",
    ]
else:
    ASAR_CANDIDATES = [
        r"D:\ZCode\resources\app.asar",
        r"C:\ZCode\resources\app.asar",
        r"%LOCALAPPDATA%\Programs\ZCode\resources\app.asar",
        r"%LOCALAPPDATA%\ZCode\resources\app.asar",
        r"%ProgramFiles%\ZCode\resources\app.asar",
    ]

LANG = "zh"   # 输出语言（main 里按 --lang / 已有 config.json 确定）


def L(zh, en):
    """双语输出：按 LANG 返回对应文案（与 overlay.js 的 L 同款约定）。"""
    return en if LANG == "en" else zh


def expand(p):
    return Path(os.path.expandvars(os.path.expanduser(p)))


def load_lang_from_config(dev):
    """已有 config.json 里存了 lang 就沿用（--dev 时 config 在仓库目录）。"""
    cfg = HERE / "config.json" if dev else DATA_DIR / "config.json"
    try:
        lang = json.loads(cfg.read_text(encoding="utf-8")).get("lang")
        return lang if lang in ("zh", "en") else None
    except (OSError, ValueError):
        return None


def asar_package_name(asar_path):
    """读 asar 内 package.json 的 name 字段，辅助识别"这是不是 ZCode 的 asar"；
    读不到（非 asar / 无 package.json / 条目 unpacked 外置）返回 None。"""
    import struct
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
    """该 asar 是否属于 ZCode：ZCode.exe 与 asar 同级，或包里 package.json 的 name 含 zcode。
    兜底扫描必须过此判定——%LOCALAPPDATA%\\Programs 下可能有其它 Electron 应用
    （如 opencode-aidesktop），按目录名猜会注入错目标：真 ZCode 永不生效。"""
    asar_path = Path(asar_path)
    if (asar_path.parent.parent / "ZCode.exe").is_file():
        return True
    name = asar_package_name(asar_path)
    return isinstance(name, str) and "zcode" in name.lower()


def find_asar():
    env = os.environ.get("ZCODE_ASAR")   # 非默认安装位置：设一次环境变量，永久免 --asar
    if env:
        p = expand(env)
        if p.is_file():
            return p
    for c in ASAR_CANDIDATES:
        p = expand(c)
        if p.is_file():
            return p
    if sys.platform == "win32":
        # 兜底：扫 %LOCALAPPDATA%\Programs 一层子目录
        # （必须过 is_zcode_app：该目录下可能有其它 Electron 应用，命中它们会注入错目标）
        prog = expand(r"%LOCALAPPDATA%\Programs")
        if prog.is_dir():
            for ch in prog.iterdir():
                p = ch / "resources" / "app.asar"
                if p.is_file() and is_zcode_app(p):
                    return p
    elif sys.platform.startswith("linux"):
        # 兜底：扫 /opt、/usr/lib、/usr/local/lib 一层子目录，目录名含 zcode 即命中
        for base in ("/opt", "/usr/lib", "/usr/local/lib"):
            b = Path(base)
            if not b.is_dir():
                continue
            try:
                children = sorted(b.iterdir())
            except OSError:
                continue
            for ch in children:
                if "zcode" not in ch.name.lower():
                    continue
                p = ch / "resources" / "app.asar"
                if p.is_file():
                    return p
    return None


def ask_asar():
    if not sys.stdin.isatty():
        return None
    try:
        s = input(L("未自动找到 ZCode，请输入 app.asar 完整路径（回车取消）: ",
                    "ZCode was not found automatically. Enter the full path to app.asar (Enter to cancel): ")).strip('" ')
    except (EOFError, KeyboardInterrupt):
        return None
    p = Path(s)
    return p if p.is_file() else None


def find_installed_asar(explicit=None):
    """卸载时定位 asar：--asar > config 记住的安装位置（数据目录与仓库 config 都查）> 自动探测 > 询问。"""
    if explicit:
        return Path(explicit)
    for cfg in (DATA_DIR / "config.json", HERE / "config.json"):
        try:
            p = json.loads(cfg.read_text(encoding="utf-8")).get("asar_path")
            if p and Path(p).is_file():
                return Path(p)
        except (OSError, ValueError):
            pass
    return find_asar() or ask_asar()


def remember_asar(asar, dev):
    """安装后把 asar 路径记进 config.json 的 asar_path 字段（其余组件忽略未知字段），
    之后的安装/卸载免传 --asar。config 定位与 load_lang_from_config 同款（--dev 在仓库目录）。"""
    cfg = HERE / "config.json" if dev else DATA_DIR / "config.json"
    try:
        data = json.loads(cfg.read_text(encoding="utf-8")) if cfg.exists() else {}
    except (OSError, ValueError):
        data = {}
    if data.get("asar_path") == str(asar):
        return
    data["asar_path"] = str(asar)
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(L(f"[目标] 已记住安装位置 → {cfg}", f"[target] install location remembered -> {cfg}"))


def chown_to_user(path):
    """sudo 运行时把产物归还给真实用户（数据目录、config、注册文件）；
    asar 属系统安装（root 所有），保持原属主不动。非 sudo 是 no-op。"""
    sudo_user = _sudo_user()
    if not sudo_user:
        return
    try:
        import pwd
        st = pwd.getpwnam(sudo_user)
        os.chown(path, st.pw_uid, st.pw_gid)
    except (KeyError, OSError, AttributeError):
        pass


def copy_runtime(dry):
    """复制运行时四件套到数据目录（无条件覆盖：overlay 的 mtime 变化会让泵 2 秒内热重载）。"""
    for name in RUNTIME_FILES:
        src = HERE / name
        assert src.is_file(), L(f"缺少运行时文件：{src}", f"Missing runtime file: {src}")
    print(L(f"[运行时] 复制 {len(RUNTIME_FILES)} 个文件 → {DATA_DIR}",
            f"[runtime] copying {len(RUNTIME_FILES)} files -> {DATA_DIR}"))
    if not dry:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        chown_to_user(DATA_DIR)
        for name in RUNTIME_FILES:
            shutil.copy2(HERE / name, DATA_DIR / name)
            chown_to_user(DATA_DIR / name)
    return True


def prepare_config(dry, dev):
    """config.json：已存在沿用；缺失时仓库有旧配置则迁移（保留 python_path），否则按模板生成。
    标准安装落在数据目录，--dev 落在仓库目录（与旧版行为一致）。
    显式给出 --lang 时写回 config（CLI/MCP 等组件沿用同一语言）。"""
    cfg = HERE / "config.json" if dev else DATA_DIR / "config.json"
    if cfg.exists():
        if args_lang:
            if not dry:
                try:
                    data = json.loads(cfg.read_text(encoding="utf-8"))
                    data["lang"] = LANG
                    cfg.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
                except (OSError, ValueError):
                    pass
            print(L(f"[配置] 沿用已有 {cfg}（lang = {LANG}）", f"[config] keeping {cfg} (lang = {LANG})"))
        else:
            print(L(f"[配置] 沿用已有 {cfg}", f"[config] keeping existing {cfg}"))
        return True
    if not dev and (HERE / "config.json").exists():
        print(L(f"[配置] 迁移旧配置 {HERE / 'config.json'} → {cfg}",
                f"[config] migrating old config {HERE / 'config.json'} -> {cfg}"))
        if not dry:
            cfg.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(HERE / "config.json", cfg)
        return True
    vals = {
        "python_path": sys.executable,
        "activity_min_ms": 1500,
        "heartbeat_ms": 30000,
        "hot_reload": True,
        "context_window": 1000000,
        "lang": LANG,
    }
    print(L(f"[配置] 生成 {cfg}（python_path = {sys.executable}）",
            f"[config] generating {cfg} (python_path = {sys.executable})"))
    if not dry:
        cfg.parent.mkdir(parents=True, exist_ok=True)
        chown_to_user(cfg.parent)
        cfg.write_text(json.dumps(vals, indent=2, ensure_ascii=False), encoding="utf-8")
        chown_to_user(cfg)
    return True


def register_mcp(dry, dev):
    """向 ~/.zcode/cli/config.json 注册 MCP server（同名覆盖即更新指向；
    旧名 token-usage/zusage 仅当指向本工具（仓库或数据目录）时迁移删除）。"""
    usage_mcp = HERE / "usage_mcp.py" if dev else DATA_DIR / "usage_mcp.py"
    entry = {"command": sys.executable, "args": [str(usage_mcp)]}
    if not ZCODE_CONFIG.is_file():
        data = {"mcp": {"servers": {}}}
    else:
        try:
            data = json.loads(ZCODE_CONFIG.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            print(L(f"[MCP] 跳过：{ZCODE_CONFIG} 不是有效 JSON（{e}），请手动注册。",
                    f"[mcp] skipped: {ZCODE_CONFIG} is not valid JSON ({e}); register manually."))
            return False
    servers = data.setdefault("mcp", {}).setdefault("servers", {})
    servers[MCP_NAME] = entry
    ours = (str(HERE / "usage_mcp.py"), str(DATA_DIR / "usage_mcp.py"))
    removed = []
    for old_name in MCP_NAME_OLD:
        old = servers.get(old_name)
        if isinstance(old, dict) and any(p in json.dumps(old) for p in ours):
            del servers[old_name]      # 旧注册指向本工具 → 迁移到新名字
            removed.append(old_name)
    removed_note = L(f"（同时移除旧注册 {', '.join(removed)}）", f" (also removed old registration {', '.join(removed)})") if removed else ""
    print(L(f"[MCP] 注册 {MCP_NAME} → {usage_mcp}", f"[mcp] registering {MCP_NAME} -> {usage_mcp}") + removed_note)
    if not dry:
        ZCODE_CONFIG.parent.mkdir(parents=True, exist_ok=True)
        chown_to_user(ZCODE_CONFIG.parent)
        if ZCODE_CONFIG.is_file():
            shutil.copy2(ZCODE_CONFIG, ZCODE_CONFIG.with_suffix(".json.zusage.bak"))
        ZCODE_CONFIG.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        chown_to_user(ZCODE_CONFIG)
    return True


def install_command(dry):
    """复制 /usage 命令模板到 ~/.zcode/commands/usage.md。"""
    COMMANDS_DIR.mkdir(parents=True, exist_ok=True)
    dst = COMMANDS_DIR / "usage.md"
    print(L(f"[命令] {dst}", f"[command] {dst}"))
    if not dry:
        shutil.copy2(HERE / "usage.command.md", dst)
        chown_to_user(COMMANDS_DIR)
        chown_to_user(dst)
    return True


def remove_mcp(dry):
    if not ZCODE_CONFIG.is_file():
        return True
    try:
        data = json.loads(ZCODE_CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return True
    servers = data.get("mcp", {}).get("servers", {})
    gone = []
    for name in (MCP_NAME, *MCP_NAME_OLD):
        if name in servers:
            del servers[name]
            gone.append(name)
    if not gone:
        return True
    print(L(f"[MCP] 移除注册：{', '.join(gone)}", f"[mcp] removing registration: {', '.join(gone)}"))
    if not dry:
        shutil.copy2(ZCODE_CONFIG, ZCODE_CONFIG.with_suffix(".json.zusage.bak"))
        ZCODE_CONFIG.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return True


def remove_command(dry):
    dst = COMMANDS_DIR / "usage.md"
    if dst.is_file():
        print(L(f"[命令] 删除 {dst}", f"[command] deleting {dst}"))
        if not dry:
            dst.unlink()
    return True


def remove_data_dir(dry):
    """删除标准数据目录（运行时副本+配置+诊断）。--dev 安装没有数据目录，此处自然跳过。"""
    if not DATA_DIR.exists():
        return True
    print(L(f"[数据] 删除数据目录 {DATA_DIR}", f"[data] deleting data directory {DATA_DIR}"))
    if not dry:
        shutil.rmtree(DATA_DIR)
    return True


def main():
    global LANG, args_lang
    # 轻量预解析：--help 打印时 LANG 必须已定，否则帮助文本恒为中文
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--lang", choices=("zh", "en"))
    pre.add_argument("--dev", action="store_true")
    pre_args, _ = pre.parse_known_args()
    LANG = pre_args.lang or load_lang_from_config(pre_args.dev) or "zh"

    ap = argparse.ArgumentParser(description=L("ZCode Token 用量状态栏 一键安装",
                                               "ZCode Token Usage Status Bar one-shot installer"))
    ap.add_argument("--asar", help=L("app.asar 路径（默认自动探测；首次安装成功后记住，之后免传）",
                                     "path to app.asar (auto-detected by default; remembered after the first install)"))
    ap.add_argument("--no-mcp", action="store_true", help=L("跳过 MCP 注册与 /usage 命令", "skip MCP registration and the /usage command"))
    ap.add_argument("--dev", action="store_true", help=L("开发模式：不复制运行时，注入直指本仓库目录（配置/诊断留在仓库）",
                                                         "dev mode: no runtime copy; injection points at this repo (config/diagnostics stay in the repo)"))
    ap.add_argument("--remove", action="store_true", help=L("卸载", "uninstall"))
    ap.add_argument("--dry-run", action="store_true", help=L("只打印动作不落盘", "print actions without writing anything"))
    ap.add_argument("--lang", choices=("zh", "en"), default=None,
                    help=L("输出语言（默认 zh；写入 config.json 供 CLI/MCP 沿用）",
                           "output language (default zh; written to config.json for CLI/MCP)"))
    args = ap.parse_args()
    args_lang = args.lang
    if args.lang:   # 显式 --lang 优先于 config.json（预解析阶段已按 config 确定过一次默认值）
        LANG = args.lang

    import patch_install as pi   # 同目录，脚本式导入即可

    if args.remove:
        if args.dry_run:
            print(L("[卸载] （dry-run）剥离 asar 注入行 + 清理 MCP 注册与 /usage 命令 + 删除数据目录",
                    "[uninstall] (dry-run) strip the asar injection line + MCP registration & /usage command cleanup + data dir removal"))
            return 0
        asar = find_installed_asar(args.asar)
        ok = True
        if asar and asar.is_file():
            print(L(f"[目标] {asar}", f"[target] {asar}"))
            pi.set_target(asar)
            ok = pi.remove()
        else:
            print(L("[卸载] 未找到 app.asar（ZCode 可能已卸载或换了位置），跳过 asar 剥离，仅清理注册与数据。",
                    "[uninstall] app.asar not found (ZCode may be uninstalled or relocated); skipping the asar strip, cleaning registration & data only."))
        if args.no_mcp:
            return 0 if ok else 1
        remove_mcp(args.dry_run)
        remove_command(args.dry_run)
        remove_data_dir(args.dry_run)
        print(L("卸载完成。", "Uninstall complete."))
        return 0 if ok else 1

    asar = Path(args.asar) if args.asar else (find_asar() or ask_asar())
    if not asar or not asar.is_file():
        if sys.platform == "win32":
            example = r"E:\Apps\ZCode\resources\app.asar"
        elif sys.platform.startswith("linux"):
            example = "/opt/ZCode/resources/app.asar"
        else:
            example = "/Applications/ZCode.app/Contents/Resources/app.asar"
        print(L(f"找不到 app.asar。用 --asar 指定，例如：python install.py --asar {example}",
                f"app.asar not found. Specify it with --asar, e.g.: python install.py --asar {example}"))
        return 1
    print(L(f"[目标] {asar}", f"[target] {asar}"))
    if (sys.platform != "win32" and os.geteuid() != 0
            and not os.access(asar, os.W_OK)):
        hint = " ".join(sys.argv[1:])
        print(L(f"[权限] {asar.parent} 不可写（系统级安装位置，Linux 上常见），需要 sudo：",
                f"[permission] {asar.parent} is not writable (a system-level install location, common on Linux); sudo is required:"))
        print(L(f"  sudo {Path(sys.executable).name} {Path(__file__).name} {hint}".rstrip(),
                f"  sudo {Path(sys.executable).name} {Path(__file__).name} {hint}".rstrip()))
        print(L("（数据目录/MCP 注册会自动落到你的用户家目录，产物归属普通用户；asar 本身保持 root 属主）",
                "(the data dir / MCP registration go to your real user home with user ownership; the asar keeps its root ownership)"))
        if not args.dry_run:
            return 1
    if not args.dry_run:
        pi.set_target(asar)
        if not args.dev:
            pi.set_runtime(DATA_DIR)   # 注入行指向数据目录副本（泵在副本目录自洽运行）

    if not args.dev:
        copy_runtime(args.dry_run)
    prepare_config(args.dry_run, args.dev)
    if args.dry_run:
        print(L("[注入] （dry-run）python patch_install.py install",
                "[inject] (dry-run) python patch_install.py install"))
        ok = True
    else:
        ok = pi.install()
        remember_asar(asar, args.dev)   # 无论注入是否收尾成功都记住（卸载/收尾要用）
    if not ok:
        print(L("asar 注入未完成，MCP/命令部分仍会继续（可稍后单独重跑 patch_install.py install）。",
                "asar injection did not finish; MCP/command parts will continue (re-run patch_install.py install later on its own)."))
    if not args.no_mcp:
        register_mcp(args.dry_run, args.dev)
        install_command(args.dry_run)
    print("\n" + L("全部完成。重启 ZCode 后窗口底部出现悬浮条；对话内可用 /usage 查询。",
                   "All done. After restarting ZCode the floating bar appears at the bottom of the window; use /usage in chat to query."))
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
