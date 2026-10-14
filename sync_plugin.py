# -*- coding: utf-8 -*-
"""把仓库根的运行时文件同步进 plugins/zcode-token-usage-statusbar/（插件打包副本）。

插件要能脱离仓库独立安装（marketplace 缓存目录里只有 plugins/<name>/ 子树），
所以插件包里必须带一份 zusage.py 等文件的副本。改了根目录文件后跑一次：

  python3 sync_plugin.py

副本与根文件不一致时退出码 1（CI 可用作漂移检查；加 --check 只查不改）。
"""
import filecmp
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).parent.resolve()
PLUGIN = ROOT / "plugins" / "zcode-token-usage-statusbar"

# 插件包需要的根文件：除 usage_mcp.py 外也会同步进运行时目录
FILES = [
    "usage_mcp.py",
    "zusage.py",
    "patch_install.py",
    "install_monitor.py",
    "inject-main.cjs",
    "plugin-updater.cjs",
    "plugin-release.cjs",
    "plugin-storage.cjs",
    "release_unpack.py",
    "runtime_lock.py",
    "overlay.js",
]


def main(check_only=False):
    missing = [f for f in FILES if not (ROOT / f).is_file()]
    if missing:
        print("根文件缺失:", ", ".join(missing))
        return 1
    PLUGIN.mkdir(parents=True, exist_ok=True)
    drift = []
    copies = [(name, name) for name in FILES]
    for name, destination in copies:
        src, dst = ROOT / name, PLUGIN / destination
        if dst.is_file() and filecmp.cmp(src, dst, shallow=False):
            continue
        drift.append(name)
        if not check_only:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            print(f"已同步 {name}")
    if check_only:
        if drift:
            print("插件副本过期:", ", ".join(drift), "（运行 python3 sync_plugin.py 更新）")
            return 1
        print("插件副本与根文件一致。")
    elif not drift:
        print("无需更新，副本已一致。")
    return 0


if __name__ == "__main__":
    sys.exit(main(check_only="--check" in sys.argv[1:]))
