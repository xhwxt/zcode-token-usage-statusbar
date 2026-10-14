"use strict";
const fs = require("fs");
const path = require("path");
const os = require("os");
const { execFile, spawn } = require("child_process");
const crypto = require("crypto");
const { isDeepStrictEqual } = require("util");
const releaseChannel = require("./plugin-release.cjs");
const { resolvePluginStorage } = require("./plugin-storage.cjs");

const PLUGIN_ID = "zcode-token-usage-statusbar@zusage-marketplace";
const INTERVAL_MS = 24 * 60 * 60 * 1000;

function compareVersions(a, b) {
  const parse = (v) => /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$/.exec(v || "");
  const aa = parse(a), bb = parse(b);
  if (!aa || !bb) throw new Error("插件版本号缺失或不是语义版本，无法确认更新状态");
  for (let i = 1; i <= 3; i++) {
    if (Number(aa[i]) !== Number(bb[i])) return Number(aa[i]) > Number(bb[i]) ? 1 : -1;
  }
  if (!aa[4] && !bb[4]) return 0;
  if (!aa[4]) return 1;
  if (!bb[4]) return -1;
  const ap = aa[4].split("."), bp = bb[4].split(".");
  for (let i = 0; i < Math.max(ap.length, bp.length); i++) {
    if (ap[i] === bp[i]) continue;
    if (ap[i] === undefined) return -1;
    if (bp[i] === undefined) return 1;
    const an = /^\d+$/.test(ap[i]), bn = /^\d+$/.test(bp[i]);
    if (an && bn) return Number(ap[i]) > Number(bp[i]) ? 1 : -1;
    if (an !== bn) return an ? -1 : 1;
    return ap[i] > bp[i] ? 1 : -1;
  }
  return 0;
}

function readJson(file, fallback) {
  try { return JSON.parse(fs.readFileSync(file, "utf8")); } catch (e) { return fallback; }
}
function readConfig(file) {
  try {
    const value = JSON.parse(fs.readFileSync(file, "utf8"));
    return value && typeof value === "object" && !Array.isArray(value) ? value : null;
  } catch (e) { return e.code === "ENOENT" ? {} : null; }
}
function writeJson(file, value) {
  const tmp = file + "." + process.pid + ".tmp";
  try {
    fs.writeFileSync(tmp, JSON.stringify(value, null, 2) + "\n");
    fs.renameSync(tmp, file);
  } finally {
    try { fs.unlinkSync(tmp); } catch (e) { }
  }
}
function runFile(command, args, options, input, onSpawn) {
  return new Promise((resolve, reject) => {
    const child = execFile(command, args, options, (error, stdout, stderr) => {
      let data;
      try { data = JSON.parse(stdout); } catch (e) { }
      if (error) {
        const detail = data && data.diagnostics && data.diagnostics.map((d) => d.message).join("; ");
        reject(new Error(detail || String(stderr || error.message).trim().slice(0, 1000)));
      } else if (!data) reject(new Error("插件管理器没有返回有效 JSON"));
      else resolve(data);
    });
    if (onSpawn) {
      try { if (child.pid) onSpawn(child.pid); }
      catch (e) { child.kill(); reject(e); }
    }
    if (input !== undefined) { child.stdin.on("error", () => {}); child.stdin.end(input); }
  });
}

function readState(file, currentTime) {
  const value = readJson(file, {});
  const saved = value && typeof value === "object" && !Array.isArray(value) ? value : {};
  const timestamp = (v) => Number.isSafeInteger(v) && v >= 0 && v <= currentTime ? v : 0;
  let pendingVersion = "";
  try { if (typeof saved.pendingVersion === "string") { compareVersions(saved.pendingVersion, saved.pendingVersion); pendingVersion = saved.pendingVersion; } } catch (e) { }
  return { checkedAt: timestamp(saved.checkedAt), lastAttempt: timestamp(saved.lastAttempt),
    pendingSync: saved.pendingSync === true, pendingVersion,
    pendingPid: Number.isSafeInteger(saved.pendingPid) && saved.pendingPid > 0 ? saved.pendingPid : 0,
    pendingRelease: saved.pendingRelease === undefined || saved.pendingRelease === null ? null : saved.pendingRelease };
}
function legacyLockBusy(runtime) {
  // An already-running wx updater cannot honor v2 locks. Respect its live owner,
  // never unlink its file, and retire old updater processes by restarting ZCode.
  const file = path.join(runtime, "plugin-update.lock");
  try {
    const owner = readJson(file, {});
    if (owner && Number.isSafeInteger(owner.pid) && owner.pid > 0) {
      try { process.kill(owner.pid, 0); return true; }
      catch (e) { return e.code !== "ESRCH"; }
    }
    return Date.now() - fs.statSync(file).mtimeMs < 600000;
  } catch (e) { if (e.code === "ENOENT") return false; throw e; }
}
function acquireUpdateLock(runtime, python, helper) {
  return new Promise((resolve, reject) => {
    const child = spawn(python, [helper, "--hold", path.join(runtime, "plugin-update-v2.lock")], {
      windowsHide: true, stdio: ["pipe", "pipe", "pipe"],
    });
    let settled = false, output = "", detail = "";
    const timer = setTimeout(() => fail(new Error("插件更新锁未返回就绪状态")), 10000);
    const fail = (error) => {
      if (settled) return;
      settled = true; clearTimeout(timer); child.stdin.destroy(); child.kill(); reject(error);
    };
    child.stdin.on("error", () => {});
    child.once("error", fail);
    child.stderr.on("data", (chunk) => { detail = (detail + chunk).slice(0, 1000); });
    child.once("close", (code) => {
      if (!settled) fail(new Error(detail.trim() || `插件更新锁进程提前退出 (${code})`));
    });
    child.stdout.on("data", (chunk) => {
      if (settled) return;
      output += chunk;
      if (output.length > 1000) { fail(new Error("插件更新锁返回无效 JSON")); return; }
      if (!output.includes("\n")) return;
      let data;
      try { data = JSON.parse(output.trim()); } catch (e) { fail(new Error("插件更新锁返回无效 JSON")); return; }
      if (!data || typeof data.locked !== "boolean" || Object.keys(data).length !== 1) {
        fail(new Error("插件更新锁返回无效 JSON")); return;
      }
      settled = true; clearTimeout(timer);
      if (!data.locked) { child.stdin.end(); resolve(null); return; }
      let releasing;
      resolve(() => {
        if (!releasing) releasing = new Promise((done) => {
          if (child.exitCode !== null || child.signalCode !== null) { done(); return; }
          child.once("close", done); child.stdin.end();
        });
        return releasing;
      });
    });
  });
}

function createUpdater(options) {
  const { runtime, home, resourcesPath, executable, notify = () => {}, now = Date.now } = options;
  const configFile = path.join(runtime, "config.json");
  const stateFile = path.join(runtime, "plugin-update.json");
  const environment = options.env || process.env;
  let storageRoot = null, localError = "";
  const storage = () => {
    const root = resolvePluginStorage(home, environment);
    if (storageRoot && storageRoot !== root) throw new Error("更新期间插件存储目录已变化，请重新启动更新器");
    storageRoot = root;
    return root;
  };
  const bundle = path.join(resourcesPath, "glm", "zcode.cjs");
  const markerFile = path.join(runtime, ".plugin-restart-required");
  const saved = readState(stateFile, now());
  let state = { phase: "idle", supported: false, auto: false, currentVersion: "", latestVersion: "",
    ...saved, restartRequired: fs.existsSync(markerFile), skipped: [], error: "", channel: "", releaseUrl: "" };
  let busy = false;
  let selectedRelease = null, selectedChannel = null;
  const channel = () => {
    const source = options.channel ? options.channel() : releaseChannel.readChannel(home, storage());
    return { kind: source.kind, repository: source.repository || "", source: source.marketplace ? source.marketplace.source : null };
  };
  const fetchRelease = options.fetchRelease || releaseChannel.fetchBytes;
  const prepareRelease = options.prepareRelease || ((release) => releaseChannel.downloadRelease(release, python(), fetchRelease));
  const readReleaseCatalog = options.readReleaseCatalog || (() => {
    const root = path.join(storage(), "marketplaces");
    if (fs.existsSync(path.join(root, ".zusage-marketplace.transaction.json"))) throw new Error("官方插件市场事务尚未完成，请稍后重试");
    return readJson(path.join(root, "zusage-marketplace", "marketplace.json"), null);
  });
  const python = () => process.env.ZUSAGE_PLUGIN_PYTHON || (readConfig(configFile) || {}).python_path || (process.platform === "win32" ? "python" : "python3");
  const helper = path.join(__dirname, "runtime_lock.py");
  const lock = options.acquireLock || (() => acquireUpdateLock(runtime, python(), helper));
  const cli = options.cli || ((args, onSpawn) => runFile(executable, [bundle, "plugins", ...args, "--json"], {
    cwd: home, env: { ...environment, HOME: home, USERPROFILE: home, ELECTRON_RUN_AS_NODE: "1", ZCODE_STORAGE_DIR: path.dirname(storage()) }, windowsHide: true,
    timeout: 120000, maxBuffer: 4 * 1024 * 1024,
  }, undefined, onSpawn));
  const sync = options.sync || ((root) => runFile(python(), [
    path.join(root, "overlay_install.py"), "sync",
  ], { cwd: home, env: { ...process.env, ZUSAGE_PLUGIN_RUNTIME: runtime },
    windowsHide: true, timeout: 30000, maxBuffer: 1024 * 1024 }));

  function installedRecord() {
    const inventory = readJson(path.join(storage(), "installed_plugins.json"), {});
    const records = inventory && inventory.plugins;
    return Array.isArray(records) ? records.find((p) => p && p.id === PLUGIN_ID && p.scope === "user"
      && typeof p.installPath === "string" && p.installPath && typeof p.version === "string") : null;
  }
  function refreshLocal() {
    const config = readConfig(configFile);
    state.auto = !!config && (!Object.prototype.hasOwnProperty.call(config, "plugin_auto_update") || config.plugin_auto_update === true);
    let record = null;
    localError = "";
    try { record = installedRecord(); } catch (error) { localError = error.message; }
    state.supported = !!(record && record.installPath && (options.cli || fs.existsSync(bundle)));
    state.currentVersion = record && record.version || "";
    state.restartRequired = fs.existsSync(markerFile);
    return record;
  }
  function publish() { state.restartRequired = fs.existsSync(markerFile); notify({ ...state, busy }); }
  function load() { Object.assign(state, readState(stateFile, now())); }
  function save() {
    writeJson(stateFile, { checkedAt: state.checkedAt, lastAttempt: state.lastAttempt,
      pendingSync: state.pendingSync, pendingVersion: state.pendingVersion, pendingPid: state.pendingPid, pendingRelease: state.pendingRelease });
  }
  function diagnostics(data) {
    const errors = (data.diagnostics || []).filter((d) => d.severity === "error" && (!d.pluginId || d.pluginId === PLUGIN_ID));
    if (errors.length) throw new Error(errors.map((d) => d.message).join("; "));
  }
  async function check(auto = false, install = false) {
    if (busy) return;
    refreshLocal();
    if (!state.supported) { state.phase = localError ? "error" : "unavailable"; state.error = localError; publish(); return; }
    busy = true;
    let release;
    try {
      if (legacyLockBusy(runtime)) return;
      release = await lock();
      if (!release || legacyLockBusy(runtime)) return;
      load();
      if (state.pendingSync && await recoverPending()) return;
      refreshLocal();
      if (auto && (!state.auto || (state.lastAttempt && now() - state.lastAttempt < INTERVAL_MS))) return;
      state.phase = "checking"; state.error = ""; publish();
      state.lastAttempt = now();
      save();
      const source = channel();
      state.channel = source.kind; state.releaseUrl = ""; selectedRelease = null; selectedChannel = source;
      if (source.kind === "release") {
        selectedRelease = await releaseChannel.checkRelease(source.repository, compareVersions, fetchRelease);
        state.releaseUrl = selectedRelease.url;
        state.latestVersion = selectedRelease.version;
        const record = installedRecord();
        if (!record) throw new Error("用户级插件安装记录不存在");
        state.currentVersion = record.version;
        const newer = compareVersions(state.latestVersion, record.version) > 0;
        state.phase = newer ? "available" : "current"; state.checkedAt = now(); save();
        if ((auto || install) && newer) {
          const overview = await cli(["list"]); diagnostics(overview);
          const installed = (overview.installed || []).find((p) => p && p.id === PLUGIN_ID && (!p.scope || p.scope === "user"));
          if (!installed) throw new Error("用户插件未注册，不能安装 Release");
          if (installed.enabled !== false) await installUpdate(auto);
        }
      } else {
        const market = await cli(["marketplace", "update", "zusage-marketplace"]);
        diagnostics(market);
        const overview = await cli(["list", "--available"]);
        diagnostics(overview);
        if (!isDeepStrictEqual(channel(), source)) throw new Error("更新期间市场来源已变化");
        const installed = (overview.installed || []).find((p) => p && p.id === PLUGIN_ID && (!p.scope || p.scope === "user"));
        const available = (overview.available || []).find((p) => p && p.id === PLUGIN_ID);
        if (!installed || !available) throw new Error("未找到已安装插件或市场中的插件，请检查市场来源");
        state.currentVersion = installed.version || "";
        state.latestVersion = available.version || "";
        const newer = compareVersions(state.latestVersion, state.currentVersion) > 0;
        state.phase = newer ? "available" : "current";
        state.checkedAt = now(); save();
        if ((auto || install) && newer && installed.enabled !== false) await installUpdate(auto);
      }
    } catch (e) {
      state.phase = "error"; state.error = e.message;
    } finally {
      if (release) await release();
      busy = false; publish();
    }
  }
  function clearPending() {
    state.pendingSync = false; state.pendingVersion = ""; state.pendingPid = 0; state.pendingRelease = null;
    save();
  }
  async function recoverPending() {
    // Legacy pendingSync meant an acknowledged CLI commit. New intents are saved
    // before launch and must prove a version change in official user inventory.
    // A CLI orphaned by parent shutdown can still be writing inventory/runtime.
    // Wait even after its version commit; normal completion clears its PID.
    if (state.pendingPid) {
      try { process.kill(state.pendingPid, 0); state.phase = "updating"; return true; }
      catch (e) { if (e.code !== "ESRCH") { state.phase = "updating"; return true; } }
      state.pendingPid = 0;
    }
    const record = installedRecord();
    if (!record) throw new Error("更新恢复所需的安装记录不存在");
    if (!state.pendingVersion || compareVersions(record.version, state.pendingVersion) > 0) {
      if (!state.pendingRelease && channel().kind === "release") throw new Error("历史更新意图缺少 Release 校验信息，停止运行时同步");
      await syncInstalled(); return true;
    }
    state.lastAttempt = 0;
    clearPending();
    state.phase = "idle";
    return false;
  }
  async function installUpdate(auto) {
    let record = refreshLocal();
    if (!record) throw new Error("插件已卸载，更新已取消");
    if (auto && !state.auto) return;
    if (!isDeepStrictEqual(channel(), selectedChannel)) throw new Error("更新期间市场来源已变化");
    compareVersions(record.version, record.version);
    let downloaded = null;
    try {
      if (selectedRelease) {
        const currentChannel = channel();
        if (!isDeepStrictEqual(currentChannel, selectedChannel) || currentChannel.kind !== "release" || currentChannel.repository !== selectedRelease.repository) throw new Error("更新期间市场来源已变化");
        downloaded = await prepareRelease(selectedRelease);
        releaseChannel.validateManifest(downloaded.manifest, selectedRelease);
        const market = await cli(["marketplace", "update", "zusage-marketplace"]); diagnostics(market);
        if (!isDeepStrictEqual(channel(), currentChannel)) throw new Error("更新期间市场来源已变化");
        releaseChannel.validateCatalog(readReleaseCatalog(), selectedRelease, downloaded.manifest);
        record = refreshLocal();
        if (!record) throw new Error("插件已卸载，更新已取消");
        if (auto && !state.auto) return;
        if (compareVersions(selectedRelease.version, record.version) <= 0) return;
        const overview = await cli(["list"]); diagnostics(overview);
        const installed = (overview.installed || []).find((p) => p && p.id === PLUGIN_ID && (!p.scope || p.scope === "user"));
        if (!installed) throw new Error("用户插件已卸载，Release 安装已取消");
        if (installed.enabled === false) return;
        if (!isDeepStrictEqual(channel(), currentChannel)) throw new Error("更新期间市场来源已变化");
        record = refreshLocal();
        if (!record) throw new Error("插件已卸载，更新已取消");
        if ((auto && !state.auto) || compareVersions(selectedRelease.version, record.version) <= 0) return;
      }
      state.phase = "updating";
      state.pendingSync = true; state.pendingVersion = record.version; state.pendingPid = 0;
      state.pendingRelease = selectedRelease ? { release: selectedRelease, manifest: downloaded.manifest, channel: channel() } : null;
      save(); publish();
      const command = selectedRelease ? ["install", PLUGIN_ID, "--scope", "user"] : ["update", PLUGIN_ID];
      try {
        let result;
        try { result = await cli(command, (pid) => { state.pendingPid = pid; save(); }); }
        finally { state.pendingPid = 0; save(); }
        diagnostics(result);
        if (result.ok !== true) throw new Error("插件管理器未确认更新成功");
      } catch (e) {
        const committed = installedRecord();
        if (committed && compareVersions(committed.version, state.pendingVersion) > 0) {
          if (selectedRelease) verifyReleaseCommit(committed);
          await syncInstalled(); return;
        }
        state.lastAttempt = 0; clearPending(); throw e;
      }
      const committed = installedRecord();
      if (!committed || compareVersions(committed.version, state.pendingVersion) <= 0) {
        state.lastAttempt = 0; clearPending(); throw new Error("插件管理器未提交新版安装记录");
      }
      if (selectedRelease) verifyReleaseCommit(committed);
      await syncInstalled();
    } finally { if (downloaded && downloaded.cleanup) downloaded.cleanup(); }
  }
  function verifyReleaseCommit(record) {
    const expected = state.pendingRelease;
    const release = expected && expected.release, manifest = expected && expected.manifest;
    if (!release || !manifest || !expected.channel || !isDeepStrictEqual(channel(), expected.channel)) throw new Error("Release 更新意图无效或市场来源已变化，停止运行时同步");
    const validated = releaseChannel.validateRelease({ tag_name: release.tag, draft: false, prerelease: false,
      assets: [release.archive, release.manifest] }, release.repository);
    if (!validated || validated.version !== release.version || expected.channel.kind !== "release" ||
        expected.channel.repository !== release.repository) throw new Error("Release 更新意图无效，停止运行时同步");
    releaseChannel.validateManifest(manifest, validated);
    if (record.version !== release.version || !record.source || record.source.url !== release.archive.browser_download_url ||
        record.source.sha256 !== manifest.asset.sha256 || record.source.source !== "url" || record.source.type !== "zip" ||
        record.source.path !== "plugins/zcode-token-usage-statusbar" ||
        (record.source.stripRoot !== undefined && record.source.stripRoot !== false) || record.source.headers !== undefined) throw new Error("官方安装结果与选定 Release 不一致，停止运行时同步");
  }
  async function recordRestart(report) {
    if (report.restartRecorded === true) return;
    const changed = Array.isArray(report.changed) ? report.changed : [];
    const cold = changed.filter((f) => f === "inject-main.cjs" || f === "plugin-updater.cjs" || f === "plugin-release.cjs" || f === "plugin-storage.cjs"
      || ((readConfig(configFile) || {}).hot_reload === false && (f === "overlay.js" || f === "zusage.py")));
    if (!cold.length) return;
    const hashes = {};
    for (const name of cold) {
      try { hashes[name] = crypto.createHash("sha256").update(fs.readFileSync(path.join(runtime, name))).digest("hex"); }
      catch (e) { if (e.code !== "ENOENT") throw e; }
    }
    await runFile(python(), [helper, "--record-restart", runtime], {
      windowsHide: true, timeout: 15000, maxBuffer: 1024 * 1024,
    }, JSON.stringify({ hashes, changed, version: state.currentVersion }));
  }
  async function syncInstalled() {
    // 更新器只信任宿主安装记录，不接收页面提供的脚本路径。
    const record = installedRecord();
    if (!record || !record.installPath) throw new Error("更新后的安装记录不存在");
    if (state.pendingRelease !== null) verifyReleaseCommit(record);
    const report = await sync(record.installPath);
    if (!report || typeof report !== "object" || Array.isArray(report)) throw new Error("运行时同步没有返回有效报告");
    state.currentVersion = record.version;
    // Current packages persist the marker themselves. This also covers old
    // packages' changed reports, before clearing durable pending coordination.
    await recordRestart(report);
    clearPending();
    state.skipped = Array.isArray(report.preserved) ? report.preserved : [];
    state.restartRequired = fs.existsSync(markerFile);
    state.phase = "updated"; state.error = "";
  }
  async function request(req) {
    if (!req) return;
    if (req.action === "auto" && typeof req.enabled === "boolean") {
      try {
        let cfg;
        try { cfg = JSON.parse(fs.readFileSync(configFile, "utf8")); }
        catch (e) { if (e.code === "ENOENT") cfg = {}; else throw e; }
        if (!cfg || typeof cfg !== "object" || Array.isArray(cfg)) throw new Error("config.json 不是对象");
        cfg.plugin_auto_update = req.enabled;
        writeJson(configFile, cfg);
        refreshLocal(); state.error = "";
        publish();
        if (req.enabled) await check(true);
      } catch (e) { state.phase = "error"; state.error = e.message; publish(); }
    } else if (req.action === "check") await check();
    else if (req.action === "update") {
      // 手动更新也先刷新版本，避免过期的“有新版”状态触发重装或降级。
      await check(false, true);
    }
  }
  async function tick() {
    refreshLocal();
    if (localError) { state.phase = "error"; state.error = localError; }
    publish();
    if (busy) return;
    load();
    if (state.supported && (state.pendingSync || (state.auto && (!state.lastAttempt || now() - state.lastAttempt >= INTERVAL_MS)))) await check(true);
  }
  refreshLocal();
  return { request, tick, status: () => ({ ...state, busy }) };
}

async function runAutoCommand(argv) {
  if (argv[0] !== "--auto") throw new Error("仅支持 --auto 后台更新模式");
  const values = {};
  for (let i = 1; i < argv.length; i += 2) {
    if (!["--resources", "--runtime"].includes(argv[i]) || !argv[i + 1]) throw new Error("无效自动更新参数");
    values[argv[i]] = argv[i + 1];
  }
  if (!values["--resources"] || !values["--runtime"]) throw new Error("缺少客户端或运行时目录");
  const updater = createUpdater({ runtime: path.resolve(values["--runtime"]), home: os.homedir(),
    resourcesPath: path.resolve(values["--resources"]), executable: process.execPath });
  await updater.tick();
  const state = updater.status();
  process.stdout.write(JSON.stringify(state) + "\n");
  if (state.phase === "error") process.exitCode = 1;
}

module.exports = { createUpdater, compareVersions, INTERVAL_MS, PLUGIN_ID, runAutoCommand };
if (require.main === module) runAutoCommand(process.argv.slice(2)).catch((e) => {
  console.error("[zusage-plugin] 自动更新失败:", e.message);
  process.exitCode = 1;
});
