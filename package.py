# -*- coding: utf-8 -*-
"""把仓库打成可离线传递的 zip（给"把包传给 AI 让它帮我装"这个场景）。

    python package.py                    # 生成 dist/zcode-token-usage-statusbar-<ver>.zip
    python package.py --out DIR          # 指定输出目录
    python package.py --no-docs          # 不带文档/截图（更小，便于弱网传递）

产物是**自包含**的：解压后 `python bootstrap.py` 就能体检 + 安装，
不需要 git、不需要联网、不需要 pip。只依赖 Python 3.8+ 标准库。

包含：运行时六件套、安装/修复脚本、README、docs、config.example.json
排除：.git、__pycache__、config.json（含本机路径）、dist 自身、*.pyc
"""

import argparse
import hashlib
import json
import os
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
NAME = "zcode-token-usage-statusbar"

# 运行时 + 安装器：缺任何一个都装不起来
CORE = [
    "bootstrap.py", "install.py", "patch_install.py", "autopatch.py",
    "install_monitor.py",
    "inject-main.cjs", "overlay.js", "zusage.py", "usage_mcp.py",
    "settings.html", "settings-preload.cjs",
    "config.example.json", "usage.command.md",
    "LICENSE",
]

DOCS = [
    "README.md", "README.zh-CN.md",
    "docs/PERF.md", "docs/USAGE.md", "docs/design-notes.md",
]
DOCS_DIRS = ["docs/tour"]     # 截图目录（整目录带）

# 绝不入包
EXCLUDE_NAMES = {".git", "__pycache__", "dist", ".zcode", "node_modules"}
EXCLUDE_FILES = {"config.json", "settings-preview.html"}
EXCLUDE_GLOBS = ["*.pyc", "*.pyo", "*.tmp", "diag-*.json", "*.zip"]


def read_version():
    """从 overlay.js 的 VERSION 取版本号，避免两处手改不同步。"""
    try:
        for line in (HERE / "overlay.js").read_text(encoding="utf-8", errors="ignore").splitlines():
            if "var VERSION =" in line:
                import re
                m = re.search(r'"([^"]+)"', line)
                if m:
                    return m.group(1)
    except OSError:
        pass
    return "unknown"


def collect(include_docs):
    files = []
    for rel in CORE:
        p = HERE / rel
        if p.is_file():
            files.append(p)
    if include_docs:
        for rel in DOCS:
            p = HERE / rel
            if p.is_file():
                files.append(p)
        for rel in DOCS_DIRS:
            d = HERE / rel
            if d.is_dir():
                files += [x for x in d.rglob("*") if x.is_file()]
    # 兜底：把 docs 下其它 md 也带上（别漏了新增文档）
    elif False:
        pass
    return files


def should_skip(rel_parts):
    for part in rel_parts:
        if part in EXCLUDE_NAMES:
            return True
    return False


def build(out_dir, include_docs):
    ver = read_version()
    out_dir.mkdir(parents=True, exist_ok=True)
    zpath = out_dir / ("%s-%s.zip" % (NAME, ver))

    files = collect(include_docs)
    seen, packed = set(), []
    for f in files:
        try:
            rel = f.relative_to(HERE)
        except ValueError:
            continue
        if should_skip(rel.parts):
            continue
        if f.name in EXCLUDE_FILES:
            continue
        if any(f.match(g) for g in EXCLUDE_GLOBS):
            continue
        key = str(rel)
        if key in seen:
            continue
        seen.add(key)
        packed.append((f, rel))

    # 附一份安装说明，解压后第一眼就能看到（AI 也会先读这个）
    guide = build_guide(ver, [str(r) for _, r in packed])

    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for f, rel in sorted(packed, key=lambda x: str(x[1])):
            z.write(f, str(rel))
        z.writestr("INSTALL-给AI看.md", guide.encode("utf-8"))

    data = zpath.read_bytes()
    return {
        "path": str(zpath),
        "version": ver,
        "files": len(packed),
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "guide_bytes": len(guide.encode("utf-8")),
    }


def build_guide(ver, names):
    """解压目录里的 INSTALL 说明。写给"拿到 zip 的 AI"看的，尽量无歧义。"""
    return """# 安装说明（给 AI 或人）

这是一个 ZCode 桌面客户端的状态条插件，版本 {ver}。
所有文件都在这个目录里，**不需要联网、不需要 git、不需要 pip**。

## 你（AI）要做的事

按顺序执行，每一步都要检查退出码：

```bash
# 1) 体检（不改任何东西）
python bootstrap.py --check

# 2) 按体检结论选一个：
python bootstrap.py            # 推荐：体检 + 按需自动修复
python bootstrap.py --install  # 完整安装/重装（含 MCP 与 /usage 命令）
python bootstrap.py --repair   # 只补注入（ZCode 升级后最常见的情况）
```

`--check` 的退出码：`0` 健康 / `1` 需要修 / `2` 环境缺失（通常是找不到 ZCode
或 Python < 3.8）。加 `--json` 可拿到机器可读结果。

## 必须知道的四件事

1. **ZCode 必须已经装好**。这个插件是往 ZCode 的 `app.asar` 里注入一行 loader，
   不是独立应用。找不到 ZCode 时会明确告诉你试过哪些路径。
2. **ZCode 正在运行会影响写 asar**（Windows 上可能报拒绝访问）。安装器会
   生成 `.tmp` 并等 ZCode 退出后收尾；最省事的做法是先退出 ZCode 再装。
3. **装完要重启 ZCode**。`overlay.js` 支持热更新，但 `inject-main.cjs` 只在
   启动时加载，界面上的东西也要重启才出现。
4. **只写两个地方**：ZCode 的 `app.asar`（加一行注入）和
   `~/.zcode/zcode-token-usage-statusbar/`（运行时副本 + 配置）。

## 卸载

```bash
python bootstrap.py --uninstall
```

只删自己加的那行注入，不依赖备份，不会把官方升级后的 asar 还原成旧版。

## 装完之后

- 状态条出现在 ZCode 对话窗口最底部；点 ⚙ → 「打开完整设置…」进独立设置窗。
- 完整用法见 `docs/USAGE.md`。
- ZCode 每次升级都会覆盖 `app.asar`，升级后重跑 `python bootstrap.py --repair`。

## 包内文件

""" + "\n".join("- `%s`" % n for n in names) + "\n"


def main(argv):
    ap = argparse.ArgumentParser(description="打包为可离线传递的 zip")
    ap.add_argument("--out", default=str(HERE / "dist"), help="输出目录")
    ap.add_argument("--no-docs", action="store_true", help="不带文档与截图")
    args = ap.parse_args(argv)

    res = build(Path(args.out), not args.no_docs)
    print("打包完成")
    print("  文件  :", res["path"])
    print("  版本  :", res["version"])
    print("  条目  : %d 个" % res["files"])
    print("  大小  : %.1f KB" % (res["bytes"] / 1024))
    print("  SHA256:", res["sha256"])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
