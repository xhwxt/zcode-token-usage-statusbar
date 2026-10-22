# 如果上游不合并

这个仓库是 [xhwxt/zcode-token-usage-statusbar](https://github.com/xhwxt/zcode-token-usage-statusbar) 的 fork。
本文件说明：**上游没合、或者压根没时间看 PR 的时候，你还能怎么用上这些改动。**

## 三条路，按省心程度排序

### 1. 直接从 Release 拿离线包（最省心）

https://github.com/suiren0219/zcode-token-usage-statusbar/releases/latest

下载 zip 解压，跑：

```bash
python bootstrap.py --check    # 先看现状
python bootstrap.py            # 体检 + 按需修复
```

不需要 git、不需要联网、不需要 pip。包内 `INSTALL-给AI看.md` 写清了每一步的退出码含义，
把包发给别的 AI 让它照着装也行。

**代价**：后续更新要自己回来下新包。

### 2. 用这个 fork 的 main 分支（想跟更新）

```bash
git clone -b perf/startup-lite https://github.com/suiren0219/zcode-token-usage-statusbar.git
cd zcode-token-usage-statusbar
python bootstrap.py
```

**代价**：和上游分叉了，上游以后修别的东西不会自动过来。

### 3. 只把需要的修复单独打上来（最保守）

如果你只想要 issue #22 那个修复、不想动别的，PR 里的改动是独立的：

```bash
git clone https://github.com/xhwxt/zcode-token-usage-statusbar.git
cd zcode-token-usage-statusbar
git fetch https://github.com/suiren0219/zcode-token-usage-statusbar.git fix/issue-22
git cherry-pick 6ea5707ca4f67ce8cd4e1e3f65be96c2b310d923
```

那个分支只有一个 commit、改一个文件、+7/-1，冲突概率很低。

## 为什么拆成两个 PR

- **#24** 只修 bug：1 个文件、+7/-1。这种尺寸的 PR 被合并的概率最高，而且它修的是
  用户能直接看见的功能缺失（切到某些会话条面全空）。
- **#23** 是功能与性能合集：设置窗、归档、打包、性能优化，十几个文件。这种规模的 PR
  上游通常需要时间审，放着不动是常态。

所以两个 PR 的内容**不互相依赖**：#24 可以单独合，#23 不合也不影响 #24 生效。

## 上游合并之后

如果上游合了其中任何一个，就回到上游版本，别再用这个 fork：

```bash
python bootstrap.py --uninstall
git clone https://github.com/xhwxt/zcode-token-usage-statusbar.git
cd zcode-token-usage-statusbar
python bootstrap.py
```

`bootstrap.py --uninstall` 只删自己加的那行 asar 注入，不依赖备份，不会把官方升级后的
新版 asar 还原成旧版。
