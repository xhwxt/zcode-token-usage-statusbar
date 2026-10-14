# 正式发布包合同

- repository：发布目标 `owner/repo`，必须用户明确选择，并与远端更新市场来源一致；不能默认把 fork 的包安装到跟踪上游的用户。
- tag：`vX.Y.Z`；version：`X.Y.Z`，数字三段，正式版，不含预发布或 build metadata。
- 发布为非 draft、非 prerelease。截图托管 tag（例如 `screenshots-v65-hover-bubble`）不属于产品发布。
- 资产：`zcode-token-usage-statusbar-vX.Y.Z.zip` 与 `release-manifest.json`。

manifest：

```json
{
  "schemaVersion": 1,
  "repository": "OWNER/REPO",
  "version": "X.Y.Z",
  "tag": "vX.Y.Z",
  "pluginId": "zcode-token-usage-statusbar@zusage-marketplace",
  "asset": {"name": "zcode-token-usage-statusbar-vX.Y.Z.zip", "size": 12345, "sha256": "64-lowercase-hex"}
}
```

ZIP 根必须有 `marketplace.json`、`LICENSE` 和 `plugins/zcode-token-usage-statusbar/` 的实际可独立安装文件。两个插件清单与市场版本/name一致。禁止绝对路径、`..`、重复条目、符号链接、设备文件、过量解压及用户配置/usage/锁/cache。发布包由工具白名单构建，根运行时代码与插件副本应字节一致。

非核心文件增删须在两个插件清单中声明相同的 `packageLayout: {"schemaVersion": 1, "files": [...]}`，files 为完整 ZIP 文件路径集，并显式更新发布工具源码白名单。校验器按固定 bootstrap 和受约束脚本/资源目录校验，不执行新包 helper；固定引导文件不能删除，未声明布局的旧包仍要求完整历史文件集。引导合同变化需另行设计迁移，不能仅递增 schemaVersion 绕过旧校验。

sha256 来自同一 Release 的 manifest，用来验证下载完整性；它不是独立签名，不能防止发布账号或 GitHub 仓库同时被攻破。信任来源是用户注册的 GitHub 仓库与 HTTPS，远端更新不得绕过更高版本比较、来源匹配和安全解压校验。


## 更新渠道清单

`build` 输出第三个本地文件 `marketplace-release.json`，不上传为 Release 资产，也不加入 ZIP。它复制市场元信息，并将唯一插件条目的 source 改为：

```json
{
  "source": "url",
  "type": "zip",
  "url": "https://github.com/OWNER/REPO/releases/download/vX.Y.Z/zcode-token-usage-statusbar-vX.Y.Z.zip",
  "sha256": "64-lowercase-hex",
  "path": "plugins/zcode-token-usage-statusbar",
  "stripRoot": false
}
```

ZIP 内仍用相对源码 source，避免清单包含自身 ZIP 的 hash 形成循环。源码根的本地市场清单保持原样。

正式 Release 完成后，获得渠道提交/推送授权再执行：
1. 选定同一仓库的渠道 ref，推荐 `plugin-releases`。先检查远端 ref 与原清单；只允许更新本插件且版本递增。
2. 在独立临时 clone/worktree 中，从远端渠道 tip 创建工作分支；新渠道则从已确认正式 tag 创建分支。不能在脏开发工作区切换或 reset。
3. 将生成清单写为该 ref 根的 `marketplace.json`，仅暂存此文件，提交并正常推送到明确 ref。不 force、不重写既有历史；推送被拒绝则重新读取远端并停止处理，不盲目重试。
4. 读取远端 `marketplace.json`，比较版本和完整 source 与生成文件，再检查两个正式资产。
5. 给新用户市场来源 `OWNER/REPO#plugin-releases`。已有用户仍追踪原 ref；不会自动迁移。原 ref 若仍是开发用相对 source，更新器应报渠道不一致，不退回开发分支安装。

Release 资产上传成功但渠道尚未发布时，检测可以发现版本，安装会安全停止。私有仓库当前不支持认证下载；不要将 gh token、URL 凭据或自定义 headers 交给更新器。
