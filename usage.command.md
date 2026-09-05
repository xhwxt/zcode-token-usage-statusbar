---
description: 显示 ZCode token 用量（当前会话/今日/近几天/按会话）
---
调用 MCP 工具 `mcp__zcode-token-usage-statusbar__token_usage`（server 名 zcode-token-usage-statusbar）查询 token 用量，把返回的报表原样整理后回复。

参数规则（$ARGUMENTS）：
- 为空或 "now" → scope=current
- "today" → scope=today
- "week" 或 "days" → scope=week
- "sessions" → scope=sessions
- "models" → scope=models
- 其他输入 → 原样作为 scope 传入（如 session:sess_f910）
