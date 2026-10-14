"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const os = require("os");
const path = require("path");
const crypto = require("crypto");
const { execFile } = require("child_process");
const { promisify } = require("util");
const { createUpdater, PLUGIN_ID } = require("../plugin-updater.cjs");
const { validateRelease, validateManifest, downloadRelease } = require("../plugin-release.cjs");

const PLUGIN = "zcode-token-usage-statusbar";
const MARKET = "zusage-marketplace";
const REPOSITORY = "ByteRax/" + PLUGIN;
const clone = (value) => JSON.parse(JSON.stringify(value));
const readJson = (file) => JSON.parse(fs.readFileSync(file, "utf8"));
const writeJson = (file, value) => fs.writeFileSync(file, JSON.stringify(value));

function fixture(t, settings = {}, customStorage = false) {
  const dir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "zusage-release-updater-")));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const home = path.join(dir, "home"), runtime = path.join(dir, "runtime");
  const defaultInventory = path.join(home, ".zcode", "cli", "plugins");
  const storageDir = path.join(dir, "custom-storage");
  const inventory = customStorage ? path.join(storageDir, "cli", "plugins") : defaultInventory;
  if (customStorage) {
    fs.mkdirSync(path.join(home, ".zcode", "cli"), { recursive: true });
    writeJson(path.join(home, ".zcode", "cli", "config.json"), { storage: { dir: storageDir } });
  }
  const marketRoot = path.join(inventory, "marketplaces", MARKET);
  for (const folder of [runtime, marketRoot, path.join(home, "installed")]) fs.mkdirSync(folder, { recursive: true });
  const configFile = path.join(runtime, "config.json"), stateFile = path.join(runtime, "plugin-update.json");
  const recordFile = path.join(inventory, "installed_plugins.json");
  const knownFile = path.join(inventory, "known_marketplaces.json");
  const catalogFile = path.join(marketRoot, "marketplace.json");
  writeJson(configFile, { python_path: "python3", lang: "en", ...settings });
  const source = { source: "github", repo: REPOSITORY, ref: "plugin-releases" };
  const record = { id: PLUGIN_ID, version: "1.0.0", scope: "user", installPath: path.join(home, "installed"),
    source: { source: "directory", path: path.join(dir, "old-package") } };
  const events = [], calls = [], syncs = [], prepared = [], states = [];
  const f = { dir, home, runtime, inventory, defaultInventory, storageDir, configFile, stateFile, recordFile, knownFile, catalogFile,
    source, record, events, calls, syncs, prepared, states, enabled: true, offline: false, cleanupCount: 0 };
  f.writeKnown = () => writeJson(knownFile, { version: 1, marketplaces: [{ id: MARKET, source: clone(source) }] });
  f.writeRecord = () => writeJson(recordFile, { version: 1, plugins: [record] });
  f.channel = () => ({ kind: "release", repository: REPOSITORY, source: clone(source) });
  f.setRelease = (version, bytes = Buffer.from("synthetic archive"), manifest = null) => {
    const base = `https://github.com/${REPOSITORY}/releases/download/v${version}/`;
    const name = `${PLUGIN}-v${version}.zip`;
    f.manifest = manifest || { schemaVersion: 1, repository: REPOSITORY, version, tag: "v" + version,
      pluginId: PLUGIN_ID, asset: { name, size: bytes.length, sha256: crypto.createHash("sha256").update(bytes).digest("hex") } };
    f.bytes = bytes;
    f.row = { tag_name: "v" + version, draft: false, prerelease: false, assets: [
      { name, state: "uploaded", size: bytes.length, browser_download_url: base + name },
      { name: "release-manifest.json", state: "uploaded", size: Buffer.byteLength(JSON.stringify(f.manifest)),
        browser_download_url: base + "release-manifest.json" },
    ] };
    f.release = validateRelease(f.row, REPOSITORY);
    validateManifest(f.manifest, f.release);
    f.catalog = { name: MARKET, plugins: [{ name: PLUGIN, version, source: { source: "url", type: "zip",
      url: f.release.archive.browser_download_url, sha256: f.manifest.asset.sha256,
      path: "plugins/" + PLUGIN, stripRoot: false } }] };
    f.rows = [f.row];
  };
  f.intent = () => ({ release: clone(f.release), manifest: clone(f.manifest), channel: f.channel() });
  f.seedPending = (pendingRelease = f.intent()) => {
    record.version = f.release.version; record.source = clone(f.catalog.plugins[0].source); f.writeRecord();
    writeJson(stateFile, { checkedAt: 100000000, lastAttempt: 100000000, pendingSync: true,
      pendingVersion: "1.0.0", pendingPid: 0, pendingRelease });
  };
  f.fetch = async (url, limit) => {
    events.push("fetch");
    assert.equal(url, `https://api.github.com/repos/${REPOSITORY}/releases?per_page=100&page=1`);
    assert.equal(limit, 4 * 1024 * 1024);
    if (f.offline) throw new Error("fixture offline");
    if (f.afterFetch) await f.afterFetch();
    return Buffer.from(JSON.stringify(f.rows));
  };
  let listCount = 0;
  const cli = async (args) => {
    calls.push([...args]); events.push(args[0] === "marketplace" ? "marketplace" : args[0]);
    if (f.offline) throw new Error("fixture CLI unavailable offline");
    if (args[0] === "list") {
      assert.deepEqual(args, ["list"]);
      if (f.afterList) await f.afterList(++listCount);
      return { installed: f.uninstalled ? [] : [{ id: PLUGIN_ID, version: record.version, scope: "user", enabled: f.enabled }], diagnostics: [] };
    }
    if (args[0] === "marketplace") {
      assert.deepEqual(args, ["marketplace", "update", MARKET]);
      if (f.marketError) throw new Error(f.marketError);
      writeJson(catalogFile, f.catalog);
      if (f.afterMarket) await f.afterMarket();
      return { diagnostics: [] };
    }
    assert.deepEqual(args, ["install", PLUGIN_ID, "--scope", "user"]);
    const intent = readJson(stateFile);
    assert.equal(intent.pendingSync, true); assert.equal(intent.pendingVersion, record.version);
    assert.deepEqual(intent.pendingRelease, f.intent());
    if (f.installErrorBeforeCommit) throw new Error("fixture installer failed before commit");
    if (!f.skipCommit) {
      record.version = f.release.version; record.source = clone(readJson(catalogFile).plugins[0].source);
      if (f.changeCommit) f.changeCommit(record);
      f.writeRecord();
    }
    if (f.afterInstall) await f.afterInstall();
    if (f.installErrorAfterCommit) throw new Error("fixture installer exited after commit");
    return { ok: true, diagnostics: [] };
  };
  const prepareRelease = async (release) => {
    events.push("prepare");
    assert.deepEqual(release, f.release);
    if (f.prepareError) throw new Error(f.prepareError);
    const manifest = clone(validateManifest(f.manifest, release));
    const directory = fs.mkdtempSync(path.join(dir, "prepared-")); prepared.push(directory);
    if (f.afterPrepare) await f.afterPrepare();
    return { directory, release, manifest, cleanup: () => {
      events.push("cleanup"); f.cleanupCount++; fs.rmSync(directory, { recursive: true, force: true });
    } };
  };
  f.options = { home, runtime, env: {}, resourcesPath: dir, executable: process.execPath, now: () => 100000000,
    fetchRelease: f.fetch, prepareRelease, cli, acquireLock: async () => () => {},
    sync: async (root) => {
      events.push("sync"); syncs.push(root); assert.equal(root, record.installPath);
      if (f.syncError) throw new Error(f.syncError);
      return { changed: [], preserved: [], restartRecorded: true };
    }, notify: (state) => states.push(state) };
  f.setRelease("1.1.0"); f.writeKnown(); f.writeRecord();
  f.restart = () => createUpdater(f.options);
  f.updater = f.restart();
  return f;
}

function assertNoInstall(f) {
  assert.equal(f.calls.filter((args) => args[0] === "install" || args[0] === "update").length, 0);
  assert.equal(f.syncs.length, 0);
}
function assertCleaned(f) {
  assert.equal(f.cleanupCount, f.prepared.length);
  for (const directory of f.prepared) assert.equal(fs.existsSync(directory), false);
}
async function assertPendingBlocked(f, expected) {
  const counts = [f.events.length, f.calls.length, f.syncs.length];
  f.offline = true;
  for (let i = 0; i < 2; i++) {
    const restored = f.restart(); await restored.tick();
    assert.equal(restored.status().phase, "error"); assert.equal(restored.status().pendingSync, true);
    assert.equal(restored.status().busy, false);
    assert.deepEqual(readJson(f.stateFile).pendingRelease, expected);
    assert.deepEqual([f.events.length, f.calls.length, f.syncs.length], counts);
  }
}

test("Release check selects the newest stable package without catalog refresh, download or install", async (t) => {
  const f = fixture(t); f.setRelease("1.9.0");
  const older = clone(f.row); f.setRelease("1.10.0");
  const old = clone(f.row); old.tag_name = "screenshots-v65-hover-bubble";
  f.rows = [older, old, { ...f.row, draft: true }, { ...f.row, prerelease: true }, f.row];
  const before = fs.readFileSync(f.recordFile);
  await f.updater.request({ action: "check" });
  assert.equal(f.updater.status().phase, "available"); assert.equal(f.updater.status().channel, "release");
  assert.equal(f.updater.status().latestVersion, "1.10.0"); assert.equal(f.updater.status().releaseUrl, f.release.url);
  assert.deepEqual(f.events, ["fetch"]); assert.deepEqual(fs.readFileSync(f.recordFile), before);
  assert.equal(fs.existsSync(f.catalogFile), false); assertNoInstall(f);
});

for (const mode of ["manual", "auto"]) {
  test(`${mode} Release install validates before user-scope commit and sync, then cleans downloads`, async (t) => {
    const f = fixture(t);
    const known = fs.readFileSync(f.knownFile), config = fs.readFileSync(f.configFile);
    if (mode === "manual") await f.updater.request({ action: "update", path: "/untrusted/ignored" });
    else await f.updater.tick();
    assert.equal(f.updater.status().phase, "updated");
    assert.deepEqual(f.events, ["fetch", "list", "prepare", "marketplace", "list", "install", "sync", "cleanup"]);
    assert.deepEqual(f.calls, [["list"], ["marketplace", "update", MARKET], ["list"], ["install", PLUGIN_ID, "--scope", "user"]]);
    assert.deepEqual(readJson(f.recordFile).plugins[0].source, f.catalog.plugins[0].source);
    assert.equal(readJson(f.recordFile).plugins[0].version, "1.1.0");
    assert.equal(readJson(f.stateFile).pendingSync, false); assert.equal(readJson(f.stateFile).pendingRelease, null);
    assert.deepEqual(fs.readFileSync(f.knownFile), known); assert.deepEqual(fs.readFileSync(f.configFile), config);
    assertCleaned(f);
  });
  test(`${mode} Release install respects a disabled plugin`, async (t) => {
    const f = fixture(t); f.enabled = false;
    if (mode === "manual") await f.updater.request({ action: "update" }); else await f.updater.tick();
    assert.equal(f.updater.status().phase, "available");
    assert.deepEqual(f.events, ["fetch", "list"]); assertNoInstall(f);
  });
}

test("automatic opt-out survives restart without network while manual Release update remains available", async (t) => {
  const f = fixture(t, { plugin_auto_update: false });
  await f.updater.tick(); await f.restart().tick(); assert.deepEqual(f.events, []);
  await f.updater.request({ action: "update" }); assert.equal(f.updater.status().phase, "updated");
  assert.equal(readJson(f.configFile).plugin_auto_update, false); assertCleaned(f);
});

for (const version of ["1.1.0", "2.0.0"]) {
  test(`Release ${version} inventory never reinstalls or downgrades to 1.1.0`, async (t) => {
    const f = fixture(t); f.record.version = version; f.writeRecord();
    await f.updater.request({ action: "update" });
    assert.equal(f.updater.status().phase, "current"); assert.deepEqual(f.events, ["fetch"]); assertNoInstall(f);
  });
}

for (const change of ["disable", "newer inventory"]) {
  test(`Release rechecks ${change} after catalog refresh and cleans without installing`, async (t) => {
    const f = fixture(t);
    f.afterMarket = () => {
      if (change === "disable") writeJson(f.configFile, { plugin_auto_update: false });
      else { f.record.version = "2.0.0"; f.writeRecord(); }
    };
    await f.updater.tick(); assertNoInstall(f); assertCleaned(f);
    assert.deepEqual(f.events, ["fetch", "list", "prepare", "marketplace", "cleanup"]);
  });
}

test("disabling auto while Release preparation awaits prevents install without cancelling cleanup", async (t) => {
  const f = fixture(t);
  let resume, entered;
  const gate = new Promise((resolve) => { resume = resolve; });
  const started = new Promise((resolve) => { entered = resolve; });
  f.afterPrepare = async () => { entered(); await gate; };
  const running = f.updater.tick();
  try { await started; await f.updater.request({ action: "auto", enabled: false }); }
  finally { resume(); await running; }
  assertNoInstall(f); assertCleaned(f); assert.equal(readJson(f.configFile).plugin_auto_update, false);
});

for (const mode of ["manual", "auto"]) {
  test(`${mode} empty formal Release listing refuses development-branch fallback`, async (t) => {
    const f = fixture(t); f.rows = [];
    if (mode === "manual") await f.updater.request({ action: "update" }); else await f.updater.tick();
    assert.equal(f.updater.status().phase, "error"); assert.match(f.updater.status().error, /正式 Release/);
    assert.deepEqual(f.events, ["fetch"]); assertNoInstall(f);
  });
}

for (const failure of ["download", "catalog fetch", "catalog hash", "catalog URL", "catalog transaction"]) {
  test(`Release ${failure} failure never invokes the installer or sync`, async (t) => {
    const f = fixture(t);
    if (failure === "download") f.prepareError = "fixture download SHA256 failure";
    if (failure === "catalog fetch") f.marketError = "fixture catalog fetch failure";
    if (failure === "catalog hash") f.catalog.plugins[0].source.sha256 = "0".repeat(64);
    if (failure === "catalog URL") f.catalog.plugins[0].source.url = "https://github.com/other/repo/releases/download/v1.1.0/package.zip";
    if (failure === "catalog transaction") writeJson(path.join(f.inventory, "marketplaces", ".zusage-marketplace.transaction.json"), {});
    await f.updater.request({ action: "update" });
    assert.equal(f.updater.status().phase, "error"); assert.equal(f.updater.status().pendingSync, false);
    assertNoInstall(f); assertCleaned(f); assert.equal(f.record.version, "1.0.0");
  });
}

for (const change of ["disabled", "uninstalled", "ref", "newer inventory"]) {
  test(`Release second inventory list rechecks ${change} before installer launch`, async (t) => {
    const f = fixture(t);
    f.afterList = (count) => {
      if (count !== 2) return;
      if (change === "disabled") f.enabled = false;
      if (change === "uninstalled") f.uninstalled = true;
      if (change === "ref") { f.source.ref = "changed-before-install"; f.writeKnown(); }
      if (change === "newer inventory") { f.record.version = "2.0.0"; f.writeRecord(); }
    };
    await f.updater.request({ action: "update" });
    if (change === "uninstalled" || change === "ref") assert.equal(f.updater.status().phase, "error");
    assertNoInstall(f); assertCleaned(f);
    assert.deepEqual(f.events, ["fetch", "list", "prepare", "marketplace", "list", "cleanup"]);
  });
}

for (const stage of ["fetch", "list", "prepare", "marketplace"]) {
  for (const change of ["repository", "directory", "ref"]) {
    test(`Release source ${change} change during ${stage} fails closed before install`, async (t) => {
      const f = fixture(t);
      const mutate = () => {
        if (change === "repository") f.source.repo = "other/" + PLUGIN;
        if (change === "directory") { delete f.source.repo; f.source.source = "directory"; f.source.path = f.dir; }
        if (change === "ref") f.source.ref = "different-release-channel";
        f.writeKnown();
      };
      f[{ fetch: "afterFetch", list: "afterList", prepare: "afterPrepare", marketplace: "afterMarket" }[stage]] = mutate;
      await f.updater.request({ action: "update" });
      assert.equal(f.updater.status().phase, "error"); assertNoInstall(f); assertCleaned(f);
    });
  }
}

const commitChanges = {
  version: (record) => { record.version = "1.2.0"; },
  hash: (record) => { record.source.sha256 = "0".repeat(64); },
  URL: (record) => { record.source.url = "https://github.com/other/repo/releases/download/v1.1.0/package.zip"; },
  type: (record) => { record.source.type = "tar"; },
  source: (record) => { record.source.source = "directory"; },
  path: (record) => { record.source.path = "plugins/other"; },
  stripRoot: (record) => { record.source.stripRoot = true; },
  headers: (record) => { record.source.headers = {}; },
};
for (const [name, change] of Object.entries(commitChanges)) {
  test(`committed Release ${name} mismatch stays pending and updater restarts never sync offline`, async (t) => {
    const f = fixture(t); f.changeCommit = change;
    const intent = f.intent();
    await f.updater.request({ action: "update" });
    assert.equal(f.updater.status().phase, "error"); assert.equal(f.updater.status().pendingSync, true);
    assert.match(f.updater.status().error, /Release/); assert.equal(f.syncs.length, 0);
    assert.deepEqual(readJson(f.stateFile).pendingRelease, intent); assertCleaned(f);
    await assertPendingBlocked(f, intent);
    assert.equal(f.calls.filter((args) => args[0] === "install").length, 1);
  });
}

test("mismatched commit remains pending when installer exits after recording a newer version", async (t) => {
  const f = fixture(t), intent = f.intent();
  f.installErrorAfterCommit = true; f.changeCommit = commitChanges.hash;
  await f.updater.request({ action: "update" });
  assert.equal(f.updater.status().phase, "error"); assert.equal(f.syncs.length, 0); assertCleaned(f);
  await assertPendingBlocked(f, intent);
});

test("market ref changed after installer commit preserves pending and blocks restart sync", async (t) => {
  const f = fixture(t), intent = f.intent();
  f.afterInstall = () => { f.source.ref = "changed-after-commit"; f.writeKnown(); };
  await f.updater.request({ action: "update" });
  assert.equal(f.updater.status().phase, "error"); assert.equal(f.syncs.length, 0); assertCleaned(f);
  await assertPendingBlocked(f, intent);
});

test("valid committed Release recovers from installer exit without reinstalling", async (t) => {
  const f = fixture(t); f.installErrorAfterCommit = true;
  await f.updater.request({ action: "update" });
  assert.equal(f.updater.status().phase, "updated"); assert.equal(f.syncs.length, 1);
  assert.equal(readJson(f.stateFile).pendingSync, false); assertCleaned(f);
});

test("valid Release pending survives sync failure and recovers offline after restart with auto disabled", async (t) => {
  const f = fixture(t); f.syncError = "fixture runtime busy";
  const intent = f.intent();
  await f.updater.request({ action: "update" });
  assert.equal(f.updater.status().phase, "error"); assert.equal(f.updater.status().pendingSync, true);
  assert.deepEqual(readJson(f.stateFile).pendingRelease, intent); assertCleaned(f);
  const events = [...f.events], calls = clone(f.calls);
  f.syncError = null; f.offline = true; writeJson(f.configFile, { plugin_auto_update: false });
  const restored = f.restart(); await restored.tick();
  assert.equal(restored.status().phase, "updated"); assert.equal(restored.status().auto, false);
  assert.deepEqual(f.events, [...events, "sync"]); assert.deepEqual(f.calls, calls);
  assert.equal(f.syncs.length, 2); assert.equal(readJson(f.stateFile).pendingRelease, null);
  await f.restart().tick(); assert.equal(f.syncs.length, 2);
});

test("valid saved Release intent recovers offline even when the catalog is absent", async (t) => {
  const f = fixture(t, { plugin_auto_update: false }); f.seedPending(); f.offline = true;
  const restored = f.restart(); await restored.tick();
  assert.equal(restored.status().phase, "updated"); assert.deepEqual(f.events, ["sync"]);
  assert.equal(fs.existsSync(f.catalogFile), false); assert.equal(readJson(f.stateFile).pendingSync, false);
});

test("live installer PID keeps committed Release pending across restarts without sync", async (t) => {
  const f = fixture(t); f.seedPending();
  const intent = readJson(f.stateFile); intent.pendingPid = process.pid; writeJson(f.stateFile, intent); f.offline = true;
  for (let i = 0; i < 2; i++) {
    const restored = f.restart(); await restored.tick();
    assert.equal(restored.status().phase, "updating"); assert.equal(restored.status().pendingSync, true);
  }
  assert.deepEqual(f.events, []); assert.equal(readJson(f.stateFile).pendingPid, process.pid);
});

for (const failure of ["exception", "success without commit"]) {
  test(`Release installer ${failure} clears uncommitted intent without sync`, async (t) => {
    const f = fixture(t);
    f.installErrorBeforeCommit = failure === "exception"; f.skipCommit = failure === "success without commit";
    await f.updater.tick();
    assert.equal(f.updater.status().phase, "error"); assert.equal(f.updater.status().pendingSync, false);
    assert.equal(f.updater.status().lastAttempt, 0); assert.equal(f.syncs.length, 0); assertCleaned(f);
    assert.equal(readJson(f.stateFile).pendingRelease, null);
    f.installErrorBeforeCommit = false; f.skipCommit = false; await f.updater.tick();
    assert.equal(f.updater.status().phase, "updated"); assert.equal(f.syncs.length, 1); assertCleaned(f);
  });
}

const intentChanges = {
  "missing channel": (intent) => { delete intent.channel; },
  "wrong channel ref": (intent) => { intent.channel.source.ref = "wrong-ref"; },
  "wrong repository": (intent) => { intent.release.repository = "other/" + PLUGIN; },
  "wrong tag": (intent) => { intent.release.tag = "screenshots-v65"; },
  "wrong manifest version": (intent) => { intent.manifest.version = "9.0.0"; },
  "wrong manifest identity": (intent) => { intent.manifest.pluginId = PLUGIN + "@other"; },
  "wrong manifest hash": (intent) => { intent.manifest.asset.sha256 = "not-a-sha256"; },
  "wrong archive size": (intent) => { intent.release.archive.size++; },
};
for (const [name, change] of Object.entries(intentChanges)) {
  test(`persisted Release intent with ${name} remains pending and never syncs`, async (t) => {
    const f = fixture(t), intent = f.intent(); change(intent); f.seedPending(intent);
    await assertPendingBlocked(f, intent);
  });
}
for (const value of [false, "damaged", [], {}, null]) {
  test(`remote pending ${JSON.stringify(value)} cannot degrade to unvalidated legacy sync`, async (t) => {
    const f = fixture(t); f.seedPending(value); await assertPendingBlocked(f, value);
  });
}

for (const change of ["ref", "repository", "directory"]) {
  test(`committed pending refuses offline recovery after registered channel ${change} changes`, async (t) => {
    const f = fixture(t), intent = f.intent(); f.seedPending(intent);
    if (change === "ref") f.source.ref = "changed-while-closed";
    if (change === "repository") f.source.repo = "other/" + PLUGIN;
    if (change === "directory") { f.source.source = "directory"; delete f.source.repo; f.source.path = f.dir; }
    f.writeKnown(); await assertPendingBlocked(f, intent);
  });
}

test("valid saved pending releases a dead installer PID and recovers offline", async (t) => {
  const f = fixture(t, { plugin_auto_update: false }); f.seedPending();
  const intent = readJson(f.stateFile); intent.pendingPid = 2147483647; writeJson(f.stateFile, intent); f.offline = true;
  const restored = f.restart(); await restored.tick();
  assert.equal(restored.status().phase, "updated"); assert.deepEqual(f.events, ["sync"]);
  assert.equal(readJson(f.stateFile).pendingPid, 0); assert.equal(readJson(f.stateFile).pendingRelease, null);
});

for (const stage of ["marketplace", "list"]) {
  test(`local source changed to GitHub during ${stage} never uses development update or Release install`, async (t) => {
    const f = fixture(t);
    delete f.source.repo; delete f.source.ref;
    f.source.source = "directory"; f.source.path = path.join(f.dir, "local-market"); f.writeKnown();
    const record = fs.readFileSync(f.recordFile);
    f.options.cli = async (args) => {
      f.calls.push([...args]); f.events.push(args[0]);
      if (args[0] === "marketplace") assert.deepEqual(args, ["marketplace", "update", MARKET]);
      else assert.deepEqual(args, ["list", "--available"]);
      if (args[0] === stage) {
        delete f.source.path;
        Object.assign(f.source, { source: "github", repo: REPOSITORY, ref: "plugin-releases" });
        f.writeKnown();
      }
      return args[0] === "marketplace" ? { diagnostics: [] } : {
        installed: [{ id: PLUGIN_ID, scope: "user", version: "1.0.0", enabled: true }],
        available: [{ id: PLUGIN_ID, version: "1.1.0" }], diagnostics: [],
      };
    };
    const updater = f.restart(); await updater.request({ action: "update" });
    assert.equal(updater.status().phase, "error"); assert.match(updater.status().error, /来源已变化/);
    assertNoInstall(f); assertCleaned(f);
    assert.deepEqual(f.calls, [["marketplace", "update", MARKET], ["list", "--available"]]);
    assert.deepEqual(f.events, ["marketplace", "list"]);
    assert.deepEqual(fs.readFileSync(f.recordFile), record); assert.equal(readJson(f.stateFile).pendingSync, false);
  });
}

test("user config custom storage supplies Release known source, inventory and catalog without default records", async (t) => {
  const f = fixture(t, {}, true);
  assert.deepEqual(f.options.env, {}); assert.equal(fs.existsSync(f.defaultInventory), false);
  assert.equal(readJson(path.join(f.home, ".zcode", "cli", "config.json")).storage.dir, f.storageDir);
  const known = fs.readFileSync(f.knownFile);
  const updater = f.restart(); assert.equal(updater.status().supported, true);
  await updater.request({ action: "check" });
  assert.equal(updater.status().phase, "available"); assertNoInstall(f);
  await updater.request({ action: "update" });
  assert.equal(updater.status().phase, "updated", updater.status().error);
  assert.deepEqual(f.calls, [["list"], ["marketplace", "update", MARKET], ["list"], ["install", PLUGIN_ID, "--scope", "user"]]);
  assert.equal(readJson(f.recordFile).plugins[0].version, f.release.version);
  assert.deepEqual(readJson(f.recordFile).plugins[0].source, f.catalog.plugins[0].source);
  assert.deepEqual(readJson(f.catalogFile), f.catalog); assert.deepEqual(fs.readFileSync(f.knownFile), known);
  assert.deepEqual(f.syncs, [f.record.installPath]); assertCleaned(f);
  assert.equal(fs.existsSync(f.defaultInventory), false);
});

test("custom storage Release pending recovers offline after auto update and sync failure without default inventory", async (t) => {
  const f = fixture(t, {}, true); f.syncError = "fixture custom runtime busy";
  const updater = f.restart(); await updater.tick();
  assert.equal(updater.status().supported, true); assert.equal(updater.status().pendingSync, true);
  assert.deepEqual(readJson(f.recordFile).plugins[0].source, f.catalog.plugins[0].source);
  assert.deepEqual(readJson(f.stateFile).pendingRelease, f.intent()); assertCleaned(f);
  const calls = clone(f.calls), events = [...f.events];
  f.offline = true; f.syncError = null; writeJson(f.configFile, { plugin_auto_update: false });
  const restored = f.restart(); await restored.tick();
  assert.equal(restored.status().phase, "updated", restored.status().error);
  assert.equal(restored.status().auto, false); assert.deepEqual(f.calls, calls);
  assert.deepEqual(f.events, [...events, "sync"]); assert.equal(f.syncs.length, 2);
  assert.equal(readJson(f.stateFile).pendingRelease, null);
  assert.equal(fs.existsSync(f.defaultInventory), false); assert.deepEqual(f.options.env, {});
});

// Real build -> downloadRelease -> Python unpack, with synthetic source files only.
// No Git, credentials, package hooks, real inventory or external HTTP is involved.
test("Release updater installs a real built ZIP through downloadRelease and unpack with stubbed HTTP", async (t) => {
  const f = fixture(t);
  const run = promisify(execFile), repositoryRoot = path.resolve(__dirname, "..");
  const env = { HOME: f.home, USERPROFILE: f.home, TMPDIR: f.dir, PYTHONDONTWRITEBYTECODE: "1",
    PATH: process.env.PATH, LANG: "en_US.UTF-8" };
  const code = `
import importlib.util, json, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location("fixture_builder", Path(sys.argv[1]) / "release_tool.py")
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)
root = Path(sys.argv[2]) / "source"
for name in tool.NAMES:
    p = root / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"not executable fixture source\\n")
plugin = {"name": tool.PLUGIN, "version": "1.1.0", "commands": "commands", "skills": "skills"}
for name in (".zcode-plugin/plugin.json", ".claude-plugin/plugin.json"):
    (root / tool.PREFIX / name).write_text(json.dumps(plugin))
(root / "marketplace.json").write_text(json.dumps({"name": "zusage-marketplace", "plugins": [
    {"name": tool.PLUGIN, "version": "1.1.0", "source": "./plugins/" + tool.PLUGIN}]}))
for name in (".mcp.json", "hooks/hooks.json"):
    (root / tool.PREFIX / name).write_text("{}")
for name in tool.SYNC_FILES:
    (root / name).write_bytes((root / tool.PREFIX / name).read_bytes())
script = (Path(sys.argv[1]) / "release_tool.py").read_bytes()
(root / "release_tool.py").write_bytes(script)
print(json.dumps(tool.build(root, sys.argv[3], Path(sys.argv[2]) / "assets")))
`;
  const built = JSON.parse((await run("python3", ["-B", "-c", code, repositoryRoot, f.dir, REPOSITORY],
    { cwd: f.dir, env, timeout: 30000 })).stdout);
  f.setRelease("1.1.0", fs.readFileSync(built.zip), readJson(built.manifest));
  f.catalog = readJson(built.catalog);
  const downloadCalls = [];
  const fetch = async (url, limit) => {
    downloadCalls.push(url);
    const bytes = url === f.release.manifest.browser_download_url ? fs.readFileSync(built.manifest)
      : url === f.release.archive.browser_download_url ? fs.readFileSync(built.zip) : null;
    assert.ok(bytes, "unexpected download URL"); assert.ok(bytes.length <= limit); return bytes;
  };
  const prepared = [];
  f.options.prepareRelease = async (release) => {
    f.events.push("prepare");
    const result = await downloadRelease(release, "python3", fetch, async (command, args) => {
      assert.equal(command, "python3"); assert.equal(args[0], path.join(repositoryRoot, "release_unpack.py"));
      return JSON.parse((await run(command, ["-B", ...args], { cwd: f.dir, env, timeout: 30000 })).stdout);
    });
    prepared.push(result.directory); t.after(result.cleanup);
    const pluginManifest = readJson(path.join(result.pluginRoot, ".zcode-plugin", "plugin.json"));
    assert.equal(pluginManifest.version, "1.1.0");
    assert.equal(fs.readFileSync(path.join(result.marketplaceRoot, "LICENSE"), "utf8"), "not executable fixture source\n");
    const cleanup = result.cleanup;
    return { ...result, cleanup: () => { f.events.push("cleanup"); cleanup(); } };
  };
  const updater = f.restart(); await updater.request({ action: "update" });
  assert.equal(updater.status().phase, "updated", updater.status().error);
  assert.deepEqual(downloadCalls, [f.release.manifest.browser_download_url, f.release.archive.browser_download_url]);
  assert.deepEqual(f.events, ["fetch", "list", "prepare", "marketplace", "list", "install", "sync", "cleanup"]);
  assert.equal(f.syncs.length, 1);
  for (const directory of prepared) assert.equal(fs.existsSync(directory), false);
});
