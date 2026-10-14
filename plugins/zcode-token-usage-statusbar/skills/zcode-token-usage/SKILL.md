---
name: zcode-token-usage
description: 查询 ZCode token 用量统计（当前会话/今日/近 N 天/按会话/按模型/按工作区）。用户询问 token 消耗、用量、花费类统计时使用。
when_to_use: 用户想了解 token 用量、消耗统计或某个会话/工作区/模型的用量明细时
license: MIT
---
用本插件提供的 MCP 工具 `token_usage`（stdio server，只读 `~/.zcode/cli/db/db.sqlite`）查询 ZCode token 用量；工具不可用时退回 CLI：`python3 "<插件根目录>/zusage.py" <子命令>`（插件根目录 = 本 SKILL.md 所在目录的上一级的上一级）。

scope 取值：current（默认，当前会话）/ today / week / days:N / sessions[:N] / models[:days] / session:<会话id前缀> / workspace:<目录名子串>。

展示要求：报表原样整理后回复；用户要详细表格时用完整数值（带千分位），不要缩写；workspace 未命中时按工具返回的候选目录引导用户换更准的子串重试。
