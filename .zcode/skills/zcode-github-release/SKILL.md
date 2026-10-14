---
name: zcode-github-release
description: "仅为当前 zcode-token-usage-statusbar 项目准备、校验并发布 GitHub 正式 Release。用户在本项目要求发布新版、创建 GitHub Release 或发布预演时使用；默认只做检查、打包和预演，提交、tag 推送、Release 与渠道发布须明确授权。"
---

# 当前项目的 GitHub 正式 Release

## 确认项目与仓库

- 本技能仅供所在仓库使用。以本技能目录向上三级得到项目根目录，验证该目录与 `git rev-parse --show-toplevel` 一致，且有 `marketplace.json`、`sync_plugin.py`、`release_tool.py`、`release_unpack.py` 和 `plugins/zcode-token-usage-statusbar/`。不要在其它项目或已安装插件缓存执行发布。
- 阅读项目指令，检查 `git status`、当前分支与 `git remote -v`，保留未提交及其它会话改动。用户只要求预演时不提交、不打 tag、不推送、不发布。
- 要求明确发布目标 `owner/repo`；核对本项目配置的 `fork` / `origin`，不默认选 origin，也不因另一仓库有权限就切换。使用 `gh repo view OWNER/REPO --json viewerPermission` 核验权限；目标只读则报告阻塞。
- 阅读 [发布包合同](references/release-contract.md)。仅正式三段版本 `X.Y.Z`、tag `vX.Y.Z`，不将截图托管 Release 或开发分支提交当作正式版本。

## 检查与打包

- 核对两份插件 manifest 与市场条目版本/name 一致。用户要求升级版本时才修改版本，不顺手修其它代码。
- 在项目根运行 `python3 -B sync_plugin.py --check`。漂移时先检查源文件，只同步项目自己的副本。
- 运行 Node、Python、速率回归及脚本语法检查。测试失败先修复或报告阻塞，不能继续发布。
- 直接使用仓库根 `release_tool.py` 与同目录可信 `release_unpack.py`，本技能不保存脚本副本：

```bash
python3 -B release_tool.py build --root /absolute/project --repo OWNER/REPO --out /new/artifact-directory
python3 -B release_tool.py verify --zip /new/artifact-directory/zcode-token-usage-statusbar-vX.Y.Z.zip --manifest /new/artifact-directory/release-manifest.json --repo OWNER/REPO
python3 -B release_tool.py publish --root /absolute/project --zip /new/artifact-directory/zcode-token-usage-statusbar-vX.Y.Z.zip --manifest /new/artifact-directory/release-manifest.json --repo OWNER/REPO --dry-run
```

- 不覆盖已有未知资产目录。检查 JSON 输出、ZIP、SHA256、版本/tag/repository。发布包仅包含市场、许可证与插件运行所需白名单文件；不包含本项目发布技能、发布工具、用量数据、用户配置、凭据、缓存、锁或截图。
- `build` 另输出 `marketplace-release.json`，是渠道清单，不是第三个 Release 资产；根目录本地 `marketplace.json` 保持不变。
- 报告将发布的仓库、版本、目标 commit、tag、资产与实际预演结果。源码未提交或 tag 未就绪时报告阻塞，不把 build 成功称为已发布。

## 正式发布门

- 仅在用户明确授权本次仓库与版本的对应动作后提交、推送 tag 或发布。创建/迁移技能不是发布授权。禁止 force push、移动/覆盖已有 tag 或覆盖已有 Release。
- 默认分支上需提交时先创建分支；仅暂存获准的发布相关文件，不使用 `git add .`。
- 所有发布文件须已提交，本地与远端正式 tag 指向该 HEAD。工具不会创建或推送 tag；缺少 tag 时仅处理获准的 tag 操作，不让 GitHub 从默认分支默默创建。
- 先执行默认发布预演。正式获准后再使用 `--publish --confirm OWNER/REPO/vX.Y.Z`。工具建 draft、上传双资产、下载逐字节核验、重查远端 commit，再转正式并核验状态。失败保留 draft，不盲删重建。
- 发布后通过 `gh release view vX.Y.Z --repo OWNER/REPO --json url,isDraft,isPrerelease,tagName,assets` 确认正式状态与两个资产，报告真实 URL。

## 发布更新渠道清单

- 正式 Release 验收通过后，获得独立渠道提交/推送授权，再将生成清单作为 `marketplace.json` 发布到同一仓库明确 ref，推荐 `plugin-releases`。预演只展示动作。
- 在独立临时 clone/worktree 操作，不切换或清理脏开发工作区。先读取远端原清单，核对插件、版本与 ref，不倒退版本或覆盖不相干内容。已有分支从远端 tip 派生，新分支从已验证 tag 派生；仅提交清单。
- 正常推送，禁止强推。之后从远端读取并核对版本、完整 ZIP URL/hash/path 与本次资产；渠道未成功时不能声称远端更新可用。
- 新用户注册 `OWNER/REPO#plugin-releases`。已有注册不会自动切换，维护者须在原 ref 发布一致清单，或由用户明确选择迁移；不修改用户市场注册。

## 收尾

- 分别报告打包、提交/tag/推送、Release、渠道与更新端验证状态。未执行就明确写未执行。
- 不安装或覆盖用户缓存，不自动重启客户端。发布成功不等于用户已安装。
