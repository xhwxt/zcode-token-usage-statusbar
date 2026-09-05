# 安全策略

本项目处理的安全面：本工具全程**只读**访问本地 `~/.zcode/cli/db/db.sqlite` 与 ZCode 安装目录的 `app.asar`（修改 asar 属安装期显式行为），不联网、不上传任何数据。

## 报告漏洞

请勿在公开 issue 中描述漏洞。使用 GitHub 的"私密漏洞上报"（仓库 Security 标签 → Report a vulnerability）或通过本仓库主页联系维护者。

## 已知安全边界

- 本项目以非官方方式修改 ZCode 客户端 asar（README"已知限制"有说明），请始终从官方发布渠道获取 ZCode 本体。
- `config.json` 含本机路径，已在 .gitignore 中排除，请勿提交。
