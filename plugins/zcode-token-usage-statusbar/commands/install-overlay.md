---
description: 为本机 ZCode 桌面客户端安装/更新悬浮用量状态条（修改 app.asar，重启客户端生效）
---
帮助用户把 zcode-token-usage-statusbar 的悬浮状态条安装到本机 ZCode 桌面客户端。

执行：用 Bash 运行安装器（插件根目录 = 本命令文件所在目录的上一级）：

    python3 "<插件根目录>/overlay_install.py" install

结果处理：

- 成功：在最终回复正文的第一行，用引用块告知用户「⚠️ 状态条已安装/重新注入，请完全退出 ZCode（所有窗口）并重新打开后生效；刷新页面或重开会话无效」。安装器可能弹出监控窗口，确认生效后会自动关闭，属正常现象。
- 输出「已安装，注入行已指向当前目录」：已装过，无需操作，照实转述。
- 失败：把安装器输出原样转述。权限不足（Linux /opt 等系统位置）时按提示建议 `sudo python install.py`（仓库一键安装器，会自动定位）。
- 若输出「运行中替换失败 / 待收尾」：说明完全退出 ZCode 后重跑同一条命令，或等监控窗口自动收尾。

补充说明（用户问起时再展开，不必每次输出）：

- 卸载：`/zcode-token-usage-statusbar:uninstall-overlay`（卸载后 hook 不再提示）。
- 自动安装开关：`~/.zcode/zcode-token-usage-statusbar/config.json` 写 `"plugin_auto_install_overlay": true` 后，新会话启动时 hook 自动安装/更新。
- 该安装只影响桌面客户端；SSH 远程会话、Web 版与本插件无关。

用户附加要求：$ARGUMENTS
