# -*- coding: utf-8 -*-
"""「ZCode Token 用量状态栏」一键安装（标准库实现，零依赖）。

用法（仓库根目录）：
  python install.py               # 全量安装：配置 → 注入 asar → 注册 MCP → /usage 命令 → 监控窗口
  python install.py --asar PATH   # 指定 app.asar（自动探测失败时用）
  python install.py --no-mcp      # 只装状态条，不动 MCP 与 /usage 命令
  python install.py --remove      # 卸载：恢复原版 asar + 移除本工具的 MCP 注册与命令
  python install.py --dry-run     # 打印将执行的动作，不写任何文件

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
ZCODE_CONFIG = Path.home() / ".zcode" / "cli" / "config.json"
COMMANDS_DIR = Path.home() / ".zcode" / "commands"
MCP_NAME = "zcode-token-usage-statusbar"   # MCP server 注册名（与仓库名一致；老安装叫 token-usage/zusage，自动迁移）
MCP_NAME_OLD = ("token-usage", "zusage")   # 历史注册名

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


def write_config(dry):
    """config.json 不存在时从模板生成（python_path 用当前解释器）。已存在则沿用。"""
    cfg = HERE / "config.json"
    if cfg.exists():
        print(f"[配置] 沿用已有 {cfg}")
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
        cfg.write_text(json.dumps(vals, indent=2), encoding="utf-8")
    return True


def register_mcp(dry):
    """向 ~/.zcode/cli/config.json 注册 MCP server（改名迁移：清掉指向本仓库的旧 token-usage）。"""
    usage_mcp = HERE / "usage_mcp.py"
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
    removed = []
    for old_name in MCP_NAME_OLD:
        old = servers.get(old_name)
        if isinstance(old, dict) and usage_mcp.name in json.dumps(old):
            del servers[old_name]      # 旧注册指向本仓库 → 迁移到新名字
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


def main():
    ap = argparse.ArgumentParser(description="ZCode Token 用量状态栏 一键安装")
    ap.add_argument("--asar", help="app.asar 路径（默认自动探测）")
    ap.add_argument("--no-mcp", action="store_true", help="跳过 MCP 注册与 /usage 命令")
    ap.add_argument("--remove", action="store_true", help="卸载")
    ap.add_argument("--dry-run", action="store_true", help="只打印动作不落盘")
    args = ap.parse_args()

    import patch_install as pi   # 同目录，脚本式导入即可

    if args.remove:
        if args.dry_run:
            print("[卸载] （dry-run）python patch_install.py remove + 清理 MCP 注册与 /usage 命令")
            return 0
        ok = pi.remove()
        if args.no_mcp:
            return 0 if ok else 1
        remove_mcp(args.dry_run)
        remove_command(args.dry_run)
        print("卸载完成。")
        return 0 if ok else 1

    asar = Path(args.asar) if args.asar else (find_asar() or ask_asar())
    if not asar or not asar.is_file():
        print("找不到 app.asar。用 --asar 指定，例如：python install.py --asar E:\\Apps\\ZCode\\resources\\app.asar")
        return 1
    print(f"[目标] {asar}")
    if not args.dry_run:
        pi.set_target(asar)

    write_config(args.dry_run)
    if args.dry_run:
        print("[注入] （dry-run）python patch_install.py install")
        ok = True
    else:
        ok = pi.install()
    if not ok:
        print("asar 注入未完成，MCP/命令部分仍会继续（可稍后单独重跑 patch_install.py install）。")
    if not args.no_mcp:
        register_mcp(args.dry_run)
        install_command(args.dry_run)
    print("\n全部完成。重启 ZCode 后输入框下方出现悬浮条；对话内可用 /usage 查询。")
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
