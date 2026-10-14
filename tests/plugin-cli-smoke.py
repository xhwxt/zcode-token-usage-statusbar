# -*- coding: utf-8 -*-
"""本机官方插件 CLI 的离线 smoke test；所有状态自动清理，仅使用临时 HOME/cwd。"""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile


EXECUTABLE = Path("/Applications/ZCode.app/Contents/MacOS/ZCode")
BUNDLE = Path("/Applications/ZCode.app/Contents/Resources/glm/zcode.cjs")
SANDBOX = Path("/usr/bin/sandbox-exec")
REPOSITORY = Path(__file__).resolve().parents[1]
MARKETPLACE = "zusage-marketplace"
PLUGIN = "zcode-token-usage-statusbar"
PLUGIN_ID = PLUGIN + "@" + MARKETPLACE
TIMEOUT = 90


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def target(rows):
    matches = [row for row in rows if row.get("id") == PLUGIN_ID]
    require(len(matches) == 1, "目标插件必须且只能出现一次")
    return matches[0]


class IsolatedCLI:
    def __init__(self, parent, name):
        self.root = parent / name
        self.home = self.root / "home"
        self.cwd = self.root / "cwd"
        self.scratch = self.root / "tmp"
        self.market = self.root / "market"
        self.plugin = self.market / "plugins" / PLUGIN
        self.inventory = self.home / ".zcode/cli/plugins"
        self.record_file = self.inventory / "installed_plugins.json"
        self.known_file = self.inventory / "known_marketplaces.json"
        self.guard = parent / (name + "-forbidden")
        for directory in (self.home, self.cwd, self.scratch, self.guard):
            directory.mkdir(parents=True)
        (self.guard / "read-sentinel").write_text("never read", encoding="utf-8")
        (self.scratch / "probe.asar").write_text("never read", encoding="utf-8")
        real_home = Path.home().resolve()
        require(not self.root.is_relative_to(real_home), "临时目录不能位于真实 HOME")
        require(not self.root.is_relative_to(REPOSITORY), "临时目录不能位于仓库")
        self.env = {
            "HOME": str(self.home),
            "USERPROFILE": str(self.home),
            "ELECTRON_RUN_AS_NODE": "1",
            "ZCODE_STORAGE_DIR": str(self.home / ".zcode"),
            "ZCODE_DATA_BASE_DIR": str(self.home),
            "ZCODE_SESSION_DB_PATH": str(self.home / ".zcode/cli/db/db.sqlite"),
            "ZCODE_LOG_DIR": str(self.home / ".zcode/cli/log"),
            "TMPDIR": str(self.scratch),
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "LANG": "en_US.UTF-8",
        }
        # 不继承宿主环境，不允许外部写入/网络；拒绝读取真实 HOME、仓库和任何 asar。
        quote = json.dumps
        self.profile = "\n".join([
            "(version 1)",
            "(allow default)",
            "(deny network*)",
            "(deny file-write*)",
            '(allow file-write* (subpath ' + quote(str(self.root)) + ') (literal "/dev/null"))',
            "(deny file-read* (subpath " + quote(str(real_home)) + ") (subpath " +
            quote(str(REPOSITORY)) + ") (subpath " + quote(str(self.guard)) +
            r') (regex #"\.asar($|/)"))',
        ])
        self.isolation = None

    def inside(self, value):
        path = Path(value)
        require(path.is_absolute() and path.resolve().is_relative_to(self.root),
                "路径越过临时目录: " + str(value))
        return path.resolve()

    def audit(self):
        for path in self.root.rglob("*"):
            self.inside(path)
        for filename in (self.record_file, self.known_file):
            if not filename.exists():
                continue
            data = read_json(filename)
            rows = data.get("plugins", data.get("marketplaces", []))
            require(isinstance(rows, list), "未知的安装记录/市场注册 JSON 形状")
            for row in rows:
                for key in ("installPath", "cachePath", "path"):
                    if row.get(key):
                        self.inside(row[key])
                source = row.get("source", {})
                if isinstance(source, dict) and source.get("source") in ("directory", "file"):
                    self.inside(source["path"])

    def run(self, args, expected=0):
        command = [str(SANDBOX), "-p", self.profile, str(EXECUTABLE), *map(str, args)]
        with subprocess.Popen(command, cwd=self.cwd, env=self.env, stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                              start_new_session=True) as process:
            try:
                stdout, stderr = process.communicate(timeout=TIMEOUT)
            except BaseException:
                # 包括超时/中断：先清理整组子进程，之后 TemporaryDirectory 才删除状态。
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate()
                raise
        self.audit()
        require(process.returncode == expected,
                "子进程退出码 {}（预期 {}）: {}\nstdout={}\nstderr={}".format(
                    process.returncode, expected, args, stdout, stderr))
        return stdout, stderr

    def cli(self, *args):
        require(self.isolation is not None, "隔离证据不足，禁止执行插件 CLI")
        stdout, stderr = self.run([BUNDLE, "plugins", *args, "--json"])
        data = json.loads(stdout)
        require(isinstance(data, dict), "CLI 没有返回 JSON 对象")
        for row in data.get("diagnostics", []):
            if row.get("path"):
                self.inside(row["path"])
            # 干净环境下内置 image-search 缺变量；原样报告，不设置 URL、不屏蔽目标错误。
            known_bundled_error = (args == ("list", "--available") and
                                   row.get("pluginId") == "image-search@zcode-plugins-official" and
                                   row.get("code") == "plugin_variable_missing" and
                                   row.get("message") == "Missing environment variable: ZCODE_BASE_URL" and
                                   bool(row.get("path")))
            require(row.get("severity") != "error" or known_bundled_error,
                    "CLI 返回错误诊断: " + json.dumps(row, ensure_ascii=False))
        for row in data.get("installed", []):
            for key in ("installPath", "rootPath", "manifestPath", "dataPath"):
                if row.get(key):
                    self.inside(row[key])
        if stderr.strip():
            print("CLI stderr: " + stderr.strip(), file=sys.stderr)
        return data

    def preflight(self):
        # 只执行 Node 内置模块探针，不加载/修改 CLI bundle，不注入插件组件。
        probe = r"""
const fs = require('original-fs'), os = require('node:os'), net = require('node:net');
const expected = JSON.parse(process.argv[1]), forbidden = process.argv[2];
const denied = (action) => { try { action(); return false; }
  catch (e) { if (!['EPERM', 'EACCES'].includes(e.code)) throw e; return true; } };
const result = { home: os.homedir(), cwd: process.cwd(), tmp: os.tmpdir(),
  envMatches: Object.entries(expected).every(([key, value]) => process.env[key] === value),
  outsideReadDenied: denied(() => fs.readFileSync(forbidden + '/read-sentinel')),
  outsideWriteDenied: denied(() => fs.writeFileSync(forbidden + '/write-sentinel', 'bad')),
  asarReadDenied: denied(() => fs.readFileSync(process.env.TMPDIR + '/probe.asar')) };
fs.writeFileSync(process.env.TMPDIR + '/allowed-sentinel', 'ok');
const server = net.createServer();
const timer = setTimeout(() => { console.error('network probe timed out'); process.exit(1); }, 5000);
server.once('error', (e) => { clearTimeout(timer);
  if (!['EPERM', 'EACCES'].includes(e.code)) throw e;
  result.networkDenied = true; console.log(JSON.stringify(result)); });
server.listen(0, '127.0.0.1', () => { clearTimeout(timer); server.close();
  console.log(JSON.stringify({ ...result, networkDenied: false })); process.exitCode = 1; });
"""
        stdout, _ = self.run(["-e", probe, json.dumps(self.env), self.guard])
        result = json.loads(stdout)
        require(result["home"] == str(self.home) and result["cwd"] == str(self.cwd) and
                result["tmp"] == str(self.scratch), "HOME/cwd/TMPDIR 未隔离")
        for key in ("envMatches", "outsideReadDenied", "outsideWriteDenied", "asarReadDenied", "networkDenied"):
            require(result.get(key) is True, "隔离预检失败: " + key)
        require(not (self.guard / "write-sentinel").exists(), "沙箱外写入没有被阻止")
        self.isolation = result

    def help(self):
        for args in (("marketplace", "add", "--help"), ("install", "--help"),
                     ("marketplace", "update", "--help"), ("list", "--help"), ("update", "--help")):
            stdout, _ = self.run([BUNDLE, "plugins", *args])
            require("--json" in stdout and "plugins" in stdout, "官方 help 不支持所需入口/选项")
        # 此 bundle 的 --help 返回全局帮助；无参数 marketplace 仅返回详细 usage，绝不注册市场。
        stdout, stderr = self.run([BUNDLE, "plugins", "marketplace"], expected=1)
        usage = stdout + stderr
        for text in ("marketplace add <source>", "install <plugin>[@marketplace]",
                     "marketplace update [name]", "list [--json] [--available]", "update <plugin>"):
            require(text in usage, "官方 usage 未确认参数: " + text)
        return {"globalHelpVerified": True, "pluginUsage": usage.strip()}

    def fixture(self, version):
        write_json(self.plugin / ".zcode-plugin/plugin.json", {"name": PLUGIN, "version": version})
        write_json(self.market / "marketplace.json", {
            "name": MARKETPLACE,
            "plugins": [{"name": PLUGIN, "version": version, "source": "./plugins/" + PLUGIN}],
        })
        files = {str(path.relative_to(self.market)) for path in self.market.rglob("*") if path.is_file()}
        require(files == {"marketplace.json", "plugins/" + PLUGIN + "/.zcode-plugin/plugin.json"},
                "最小市场混入了组件或安装器")

    def installation(self, version, json_sync=False):
        data = read_json(self.record_file)
        row = target(data["plugins"])
        require(row["scope"] == "user" and row["version"] == version, "安装记录版本/scope 错误")
        path = self.inside(row["installPath"])
        require(path == self.inventory / "cache" / MARKETPLACE / PLUGIN / version,
                "安装记录没有指向预期版本缓存")
        manifest = read_json(path / ".zcode-plugin/plugin.json")
        require(manifest == {"name": PLUGIN, "version": version}, "缓存 manifest 不匹配最小 fixture")
        expected_files = {".zcode-plugin/plugin.json"}
        if json_sync:
            expected_files.add("overlay_install.py")
            require((path / "overlay_install.py").read_text(encoding="utf-8") ==
                    'import json\nprint(json.dumps({"changed": [], "preserved": [], "version": "1.1.0"}))\n',
                    "测试同步脚本出现额外行为")
        require({str(p.relative_to(path)) for p in path.rglob("*") if p.is_file()} ==
                expected_files, "插件缓存混入可执行组件")
        return {"recordFile": str(self.record_file), "recordKeys": list(data),
                "record": row, "cacheManifest": manifest}

    def overview(self, installed_version, available_version):
        data = self.cli("list", "--available")
        installed, available = target(data["installed"]), target(data["available"])
        require(installed["version"] == installed_version, "list 的 installed 版本错误")
        require(available["version"] == available_version and available["installed"] is True,
                "list 的 available 版本/installed 标记错误")
        for key in ("rootPath", "manifestPath", "dataPath"):
            self.inside(installed[key])
        for key in ("commandRootCount", "skillCount", "skillRootCount"):
            require(installed[key] == 0, "fixture 被加载成了命令/技能")
        for key in ("declaredMcpServerNames", "mcpServerNames", "hookDetails"):
            require(installed[key] == [], "fixture 含 MCP/hook")
        return {"keys": list(data), "installed": installed, "available": available,
                "diagnostics": data["diagnostics"], "installedCount": len(data["installed"]),
                "availableCount": len(data["available"])}

    def setup_install(self):
        self.preflight()
        help_result = self.help()
        self.fixture("1.0.0")
        added = self.cli("marketplace", "add", str(self.market))
        require(added["id"] == MARKETPLACE and added["pluginCount"] == 1, "市场添加结果错误")
        require(added["source"] == {"source": "directory", "path": str(self.market)},
                "市场不是临时绝对目录来源")
        installed = self.cli("install", PLUGIN_ID)
        require(installed["ok"] is True and target(installed["installed"])["version"] == "1.0.0",
                "初次安装失败")
        require(installed["dependencyClosure"] == [PLUGIN_ID], "最小 fixture 出现依赖")
        snapshot = self.installation("1.0.0")
        return {"isolation": self.isolation, "help": help_result, "marketplaceAdd": added,
                "install": installed, "beforeRecord": snapshot}


def direct_smoke(cli):
    report = cli.setup_install()
    cli.fixture("1.1.0")
    refreshed = cli.cli("marketplace", "update", MARKETPLACE)
    require(refreshed["ok"] is True and [m["id"] for m in refreshed["marketplaces"]] == [MARKETPLACE],
            "市场刷新不是指定的单一本地市场")
    report["marketplaceUpdate"] = refreshed
    report["beforeUpdateList"] = cli.overview("1.0.0", "1.1.0")
    require(cli.installation("1.0.0") == report["beforeRecord"], "市场刷新错误地修改了安装记录/cache")
    catalogs = [p for p in cli.inventory.rglob("marketplace.json") if read_json(p).get("name") == MARKETPLACE]
    require(len(catalogs) == 1, "无法定位唯一临时市场缓存")
    cached = read_json(catalogs[0])
    require(cached["plugins"][0]["version"] == "1.1.0", "市场缓存 catalog 未升级")
    report["catalogCache"] = str(cli.inside(catalogs[0]))
    updated = cli.cli("update", PLUGIN_ID)
    require(updated["ok"] is True and updated["pluginId"] == PLUGIN_ID and
            updated["previousVersion"] == "1.0.0" and updated["version"] == "1.1.0",
            "官方更新 JSON 未确认 1.0.0 -> 1.1.0")
    require(target(updated["installed"])["version"] == "1.1.0", "更新响应 installed 版本错误")
    report["update"] = updated
    report["afterRecord"] = cli.installation("1.1.0")
    require(report["afterRecord"]["record"]["installPath"] != report["beforeRecord"]["record"]["installPath"],
            "安装记录/cache 路径未更新")
    report["afterUpdateList"] = cli.overview("1.1.0", "1.1.0")
    report["registeredMarketplaces"] = [m["id"] for m in read_json(cli.known_file)["marketplaces"]]
    return report


def updater_smoke(cli):
    report = cli.setup_install()
    cli.fixture("1.1.0")
    runtime = cli.root / "runtime"
    write_json(runtime / "config.json", {"plugin_auto_update": False})
    cli.env["ZUSAGE_PLUGIN_PYTHON"] = sys.executable
    # 仅复制被测主模块到 temp，沙箱子进程不读取仓库；market/plugin 仍只有两个 JSON 文件。
    module = cli.root / "plugin-updater.cjs"
    module.write_bytes((REPOSITORY / "plugin-updater.cjs").read_bytes())
    (module.parent / "plugin-storage.cjs").write_bytes((REPOSITORY / "plugin-storage.cjs").read_bytes())
    (module.parent / "plugin-release.cjs").write_bytes((REPOSITORY / "plugin-release.cjs").read_bytes())
    (module.parent / "runtime_lock.py").write_bytes((REPOSITORY / "runtime_lock.py").read_bytes())
    (runtime / "runtime_lock.py").write_bytes((REPOSITORY / "runtime_lock.py").read_bytes())
    harness = r"""
const fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict');
const { promisify } = require('node:util'), execFile = promisify(require('node:child_process').execFile);
const opts = JSON.parse(process.argv[1]);
const { createUpdater, PLUGIN_ID } = require(opts.module);
const calls = [], states = [], syncRoots = [];
const run = async (args) => { const { stdout } = await execFile(opts.sandbox,
  ['-p', opts.profile, opts.executable, ...args],
  { cwd: opts.cwd, env: opts.env, timeout: 90000, maxBuffer: 4 * 1024 * 1024 });
  return JSON.parse(stdout); };
const updater = createUpdater({ runtime: opts.runtime, home: opts.home,
  resourcesPath: opts.resourcesPath, executable: opts.executable,
  notify: (state) => states.push(state),
  cli: async (args) => { const data = await run([opts.bundle, 'plugins', ...args, '--json']);
    calls.push({ args, keys: Object.keys(data), ...(data.version ? { version: data.version } : {}),
      ...(data.ok !== undefined ? { ok: data.ok } : {}) }); return data; },
  sync: async (root) => { assert.ok(path.resolve(root).startsWith(opts.home + path.sep));
    assert.equal(JSON.parse(fs.readFileSync(path.join(root, '.zcode-plugin/plugin.json'))).version, '1.1.0');
    syncRoots.push(root);
    // JSON-only sync 子进程：不加载任何 overlay 安装器，不访问 asar。
    return run(['-e', 'console.log(JSON.stringify({changed: [], skipped: []}))']); } });
(async () => { await updater.request({ action: 'check' });
  const checked = updater.status(); assert.equal(checked.phase, 'available');
  assert.equal(checked.currentVersion, '1.0.0'); assert.equal(checked.latestVersion, '1.1.0');
  await updater.request({ action: 'update' }); const updated = updater.status();
  assert.equal(updated.phase, 'updated'); assert.equal(updated.currentVersion, '1.1.0');
  assert.equal(updated.busy, false); assert.equal(updated.error, '');
  assert.equal(syncRoots.length, 1);
  assert.deepEqual(calls.map((c) => c.args), [
    ['marketplace', 'update', 'zusage-marketplace'], ['list', '--available'],
    ['marketplace', 'update', 'zusage-marketplace'], ['list', '--available'], ['update', PLUGIN_ID] ]);
  console.log(JSON.stringify({ checked, updated, calls, syncRoots, states,
    syncMode: 'JSON-only child; no overlay installer' }));
})().catch((e) => { console.error(e.stack); process.exitCode = 1; });
"""
    options = {"module": str(module), "runtime": str(runtime), "home": str(cli.home),
               "cwd": str(cli.cwd), "env": cli.env, "sandbox": str(SANDBOX), "profile": cli.profile,
               "executable": str(EXECUTABLE), "bundle": str(BUNDLE),
               "resourcesPath": str(BUNDLE.parent.parent)}
    stdout, _ = cli.run(["-e", harness, json.dumps(options)])
    report["integration"] = json.loads(stdout)
    require(report["integration"]["updated"]["phase"] == "updated", "真实主模块未进入 updated")
    for root in report["integration"]["syncRoots"]:
        cli.inside(root)
    report["afterRecord"] = cli.installation("1.1.0")
    report["afterUpdateList"] = cli.overview("1.1.0", "1.1.0")
    return report


def standalone_auto_smoke(cli):
    report = cli.setup_install()
    cli.fixture("1.1.0")
    # 仅提供 JSON 同步脚本，模拟 loader 完全未加载时的 SessionStart 后台进程。
    (cli.plugin / "overlay_install.py").write_text(
        'import json\nprint(json.dumps({"changed": [], "preserved": [], "version": "1.1.0"}))\n',
        encoding="utf-8")
    runtime = cli.root / "runtime"
    write_json(runtime / "config.json", {"python_path": sys.executable})
    updater = runtime / "plugin-updater.cjs"
    updater.write_bytes((REPOSITORY / "plugin-updater.cjs").read_bytes())
    (runtime / "plugin-storage.cjs").write_bytes((REPOSITORY / "plugin-storage.cjs").read_bytes())
    (runtime / "plugin-release.cjs").write_bytes((REPOSITORY / "plugin-release.cjs").read_bytes())
    (runtime / "runtime_lock.py").write_bytes((REPOSITORY / "runtime_lock.py").read_bytes())
    stdout, _ = cli.run([updater, "--auto", "--resources", BUNDLE.parent.parent, "--runtime", runtime])
    state = json.loads(stdout)
    require(state["auto"] is True, "缺失自动更新配置未默认开启")
    require(state["phase"] == "updated" and state["currentVersion"] == "1.1.0", "无 loader 后台更新未成功")
    report["standaloneAuto"] = state
    report["afterRecord"] = cli.installation("1.1.0", json_sync=True)
    stdout, _ = cli.run([updater, "--auto", "--resources", BUNDLE.parent.parent, "--runtime", runtime])
    require(json.loads(stdout)["phase"] == "idle", "后台自动更新没有保留 24 小时限频")
    require(not (runtime / "plugin-update.lock").exists(), "不应创建旧版存在性锁")
    probe = subprocess.run([sys.executable, str(runtime / "runtime_lock.py"), "--hold",
                            str(runtime / "plugin-update-v2.lock")],
                           stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10)
    require(probe.returncode == 0 and json.loads(probe.stdout)["locked"] is True,
            "后台更新结束后 OS 锁未释放")
    return report


def main():
    require(sys.platform == "darwin", "仅支持带系统 sandbox-exec 的 macOS；不进行无沙箱回退")
    for path in (EXECUTABLE, BUNDLE, SANDBOX):
        require(path.is_file(), "本机必需文件不存在: " + str(path))
    require(os.access(EXECUTABLE, os.X_OK) and os.access(SANDBOX, os.X_OK), "隔离执行器不可用")
    with tempfile.TemporaryDirectory(prefix="zusage-plugin-cli-smoke-") as temporary:
        parent = Path(temporary).resolve()
        result = {"direct": direct_smoke(IsolatedCLI(parent, "direct")),
                  "updater": updater_smoke(IsolatedCLI(parent, "updater")),
                  "standaloneAuto": standalone_auto_smoke(IsolatedCLI(parent, "standalone"))}
    require(not parent.exists(), "临时目录未自动清理")
    print(json.dumps({"ok": True, "temporaryRoot": str(parent), "temporaryRootRemoved": True,
                      "cliTimeoutSeconds": TIMEOUT, "results": result}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("FAIL（停止执行，无隔离回退）: " + str(error), file=sys.stderr)
        sys.exit(1)
