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
  python install.py --asar PATH   # 指定 app.asar（自动探测失败时用）
  python install.py --no-mcp      # 只装状态条，不动 MCP 与 /usage 命令
  python install.py --dev         # 开发模式：不复制运行时，注入直指本仓库目录，配置/诊断留在仓库
                                  #   （作者迭代用，改仓库文件即时热更新；与标准形态重跑 install 即互切）
  python install.py --remove      # 卸载：恢复原版 asar + 移除 MCP 注册与命令 + 删除数据目录
  python install.py --dry-run     # 打印将执行的动作，不写任何文件

升级：git pull 后重跑 python install.py —— 注入行不变则 asar 不重打包（秒级），
overlay 副本刷新后由泵 2 秒内热重载；改了 inject-main.cjs（泵）才需要重启 ZCode。

ZCode 安装位置自动探测：常见目录（D:\\ZCode、C:\\ZCode、%LOCALAPPDATA%\\Programs 等）
下找 resources\\app.asar；失败且终端可交互时询问，或用 --asar 指定。
"""
import argparse
import json
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).parent.resolve()
DATA_DIR = Path.home() / ".zcode" / "zcode-token-usage-statusbar"   # 标准安装的数据/运行目录
ZCODE_CONFIG = Path.home() / ".zcode" / "cli" / "config.json"
COMMANDS_DIR = Path.home() / ".zcode" / "commands"
MCP_NAME = "zcode-token-usage-statusbar"   # MCP server 注册名（与仓库名一致；老安装叫 token-usage/zusage，自动迁移）
MCP_NAME_OLD = ("token-usage", "zusage")   # 历史注册名
RUNTIME_FILES = ("inject-main.cjs", "overlay.js", "zusage.py", "usage_mcp.py")

# ZCode 安装位置候选：resources/app.asar 存在即命中（按序探测）
ASAR_CANDIDATES = [
    r"D:\ZCode\resources\app.asar",
    r"C:\ZCode\resources\app.asar",
    r"%LOCALAPPDATA%\Programs\ZCode\resources\app.asar",
    r"%LOCALAPPDATA%\Programs\zcode\resources\app.asar",
    r"%LOCALAPPDATA%\ZCode\resources\app.asar",
    r"%ProgramFiles%\ZCode\resources\app.asar",
]


def expand(p):
    return Path(os.path.expandvars(os.path.expanduser(p)))


def find_asar():
    for c in ASAR_CANDIDATES:
        p = expand(c)
        if p.is_file():
            return p
    # 兜底：扫 %LOCALAPPDATA%\Programs 一层子目录
    prog = expand(r"%LOCALAPPDATA%\Programs")
    if prog.is_dir():
        for ch in prog.iterdir():
            p = ch / "resources" / "app.asar"
            if p.is_file():
                return p
    return None


def ask_asar():
    if not sys.stdin.isatty():
        return None
    try:
        s = input("未自动找到 ZCode，请输入 app.asar 完整路径（回车取消）: ").strip('" ')
    except (EOFError, KeyboardInterrupt):
        return None
    p = Path(s)
    return p if p.is_file() else None


def copy_runtime(dry):
    """复制运行时四件套到数据目录（无条件覆盖：overlay 的 mtime 变化会让泵 2 秒内热重载）。"""
    for name in RUNTIME_FILES:
        src = HERE / name
        assert src.is_file(), f"缺少运行时文件：{src}"
    print(f"[运行时] 复制 {len(RUNTIME_FILES)} 个文件 → {DATA_DIR}")
    if not dry:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        for name in RUNTIME_FILES:
            shutil.copy2(HERE / name, DATA_DIR / name)
    return True


def prepare_config(dry, dev):
    """config.json：已存在沿用；缺失时仓库有旧配置则迁移（保留 python_path），否则按模板生成。
    标准安装落在数据目录，--dev 落在仓库目录（与旧版行为一致）。"""
    cfg = HERE / "config.json" if dev else DATA_DIR / "config.json"
    if cfg.exists():
        print(f"[配置] 沿用已有 {cfg}")
        return True
    if not dev and (HERE / "config.json").exists():
        print(f"[配置] 迁移旧配置 {HERE / 'config.json'} → {cfg}")
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
    }
    print(f"[配置] 生成 {cfg}（python_path = {sys.executable}）")
    if not dry:
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(json.dumps(vals, indent=2), encoding="utf-8")
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
            print(f"[MCP] 跳过：{ZCODE_CONFIG} 不是有效 JSON（{e}），请手动注册。")
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
    print(f"[MCP] 注册 {MCP_NAME} → {usage_mcp}" + (f"（同时移除旧注册 {', '.join(removed)}）" if removed else ""))
    if not dry:
        ZCODE_CONFIG.parent.mkdir(parents=True, exist_ok=True)
        if ZCODE_CONFIG.is_file():
            shutil.copy2(ZCODE_CONFIG, ZCODE_CONFIG.with_suffix(".json.zusage.bak"))
        ZCODE_CONFIG.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return True


def install_command(dry):
    """复制 /usage 命令模板到 ~/.zcode/commands/usage.md。"""
    COMMANDS_DIR.mkdir(parents=True, exist_ok=True)
    dst = COMMANDS_DIR / "usage.md"
    print(f"[命令] {dst}")
    if not dry:
        shutil.copy2(HERE / "usage.command.md", dst)
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
    print(f"[MCP] 移除注册：{', '.join(gone)}")
    if not dry:
        shutil.copy2(ZCODE_CONFIG, ZCODE_CONFIG.with_suffix(".json.zusage.bak"))
        ZCODE_CONFIG.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return True


def remove_command(dry):
    dst = COMMANDS_DIR / "usage.md"
    if dst.is_file():
        print(f"[命令] 删除 {dst}")
        if not dry:
            dst.unlink()
    return True


def remove_data_dir(dry):
    """删除标准数据目录（运行时副本+配置+诊断）。--dev 安装没有数据目录，此处自然跳过。"""
    if not DATA_DIR.exists():
        return True
    print(f"[数据] 删除数据目录 {DATA_DIR}")
    if not dry:
        shutil.rmtree(DATA_DIR)
    return True


def main():
    ap = argparse.ArgumentParser(description="ZCode Token 用量状态栏 一键安装")
    ap.add_argument("--asar", help="app.asar 路径（默认自动探测）")
    ap.add_argument("--no-mcp", action="store_true", help="跳过 MCP 注册与 /usage 命令")
    ap.add_argument("--dev", action="store_true", help="开发模式：不复制运行时，注入直指本仓库目录（配置/诊断留在仓库）")
    ap.add_argument("--remove", action="store_true", help="卸载")
    ap.add_argument("--dry-run", action="store_true", help="只打印动作不落盘")
    args = ap.parse_args()

    import patch_install as pi   # 同目录，脚本式导入即可

    if args.remove:
        if args.dry_run:
            print("[卸载] （dry-run）python patch_install.py remove + 清理 MCP 注册与 /usage 命令 + 删除数据目录")
            return 0
        ok = pi.remove()
        if args.no_mcp:
            return 0 if ok else 1
        remove_mcp(args.dry_run)
        remove_command(args.dry_run)
        remove_data_dir(args.dry_run)
        print("卸载完成。")
        return 0 if ok else 1

    asar = Path(args.asar) if args.asar else (find_asar() or ask_asar())
    if not asar or not asar.is_file():
        print("找不到 app.asar。用 --asar 指定，例如：python install.py --asar E:\\Apps\\ZCode\\resources\\app.asar")
        return 1
    print(f"[目标] {asar}")
    if not args.dry_run:
        pi.set_target(asar)
        if not args.dev:
            pi.set_runtime(DATA_DIR)   # 注入行指向数据目录副本（泵在副本目录自洽运行）

    if not args.dev:
        copy_runtime(args.dry_run)
    prepare_config(args.dry_run, args.dev)
    if args.dry_run:
        print("[注入] （dry-run）python patch_install.py install")
        ok = True
    else:
        ok = pi.install()
    if not ok:
        print("asar 注入未完成，MCP/命令部分仍会继续（可稍后单独重跑 patch_install.py install）。")
    if not args.no_mcp:
        register_mcp(args.dry_run, args.dev)
        install_command(args.dry_run)
    print("\n全部完成。重启 ZCode 后输入框下方出现悬浮条；对话内可用 /usage 查询。")
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
