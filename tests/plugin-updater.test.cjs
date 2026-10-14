"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const os = require("os");
const path = require("path");
const { createUpdater, compareVersions, INTERVAL_MS, PLUGIN_ID } = require("../plugin-updater.cjs");

function fixture(t, settings = {}) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "zusage-update-test-"));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const runtime = path.join(dir, "runtime");
  const inventory = path.join(dir, ".zcode", "cli", "plugins");
  fs.mkdirSync(runtime); fs.mkdirSync(inventory, { recursive: true });
  const recordFile = path.join(inventory, "installed_plugins.json");
  const configFile = path.join(runtime, "config.json");
  fs.writeFileSync(configFile, JSON.stringify({ python_path: "python3", lang: "en", remote: { enabled: false }, ...settings }));
  const record = { id: PLUGIN_ID, version: "1.0.0", scope: "user", installPath: path.join(dir, "installed") };
  const writeRecord = () => fs.writeFileSync(recordFile, JSON.stringify({ version: 1, plugins: [record] }));
  writeRecord();
  fs.writeFileSync(path.join(inventory, "known_marketplaces.json"), JSON.stringify({ marketplaces: [
    { id: "zusage-marketplace", source: { source: "directory", path: dir } },
  ] }));
  const calls = [], states = [], syncs = [];
  let time = 100000000, version = "1.1.0", enabled = true, error = null, missing = false, changed = [], skipped = [];
  const cli = async (args) => {
    calls.push(args);
    if (error) throw new Error(error);
    if (args[0] === "marketplace") return { diagnostics: [] };
    if (args[0] === "list") return {
      installed: [{ id: PLUGIN_ID, version: record.version, enabled }],
      available: missing ? [] : [{ id: PLUGIN_ID, version }], diagnostics: [],
    };
    if (args[0] === "update") { record.version = version; writeRecord(); return { ok: true, version, diagnostics: [] }; }
    throw new Error("Unexpected CLI command");
  };
  const options = { runtime, home: dir, resourcesPath: dir, executable: process.execPath, now: () => time,
    cli, sync: async (root) => { syncs.push(root); return { changed, skipped, preserved: skipped }; }, notify: (s) => states.push(s) };
  return { updater: createUpdater(options), options, runtime, configFile, recordFile, calls, states, syncs, record,
    set: (values) => { if ("version" in values) version = values.version; if ("error" in values) error = values.error;
      if ("missing" in values) missing = values.missing; if ("enabled" in values) enabled = values.enabled;
      if ("changed" in values) changed = values.changed; if ("skipped" in values) skipped = values.skipped; },
    advance: (ms) => { time += ms; }, writeRecord };
}

test("semantic versions compare numeric components, prereleases and build metadata", () => {
  assert.equal(compareVersions("1.10.0", "1.9.0"), 1);
  assert.equal(compareVersions("1.1.0", "1.1.0-beta.2"), 1);
  assert.equal(compareVersions("1.1.0-beta.10", "1.1.0-beta.2"), 1);
  assert.equal(compareVersions("1.1.0-1", "1.1.0-beta"), -1);
  assert.equal(compareVersions("1.1.0+new", "1.1.0+old"), 0);
  assert.throws(() => compareVersions("", "1.0.0"));
});
test("missing auto setting defaults to on and tick checks plus installs", async (t) => {
  const f = fixture(t); const before = fs.readFileSync(f.configFile, "utf8");
  await f.updater.tick();
  assert.equal(f.updater.status().auto, true); assert.equal(f.updater.status().phase, "updated");
  assert.deepEqual(f.calls, [["marketplace", "update", "zusage-marketplace"], ["list", "--available"], ["update", PLUGIN_ID]]);
  assert.equal(fs.readFileSync(f.configFile, "utf8"), before);
});
test("missing config defaults to on without writing unrelated settings", async (t) => {
  const f = fixture(t); fs.unlinkSync(f.configFile);
  await f.updater.tick();
  assert.equal(f.updater.status().auto, true); assert.equal(f.updater.status().phase, "updated");
  assert.equal(fs.existsSync(f.configFile), false);
});
test("explicit false stays off across restarts and tick does not contact marketplace", async (t) => {
  const f = fixture(t, { plugin_auto_update: false });
  await f.updater.tick(); await createUpdater(f.options).tick();
  assert.equal(f.calls.length, 0); assert.equal(f.updater.status().auto, false);
  assert.equal(JSON.parse(fs.readFileSync(f.configFile)).plugin_auto_update, false);
});
test("corrupt, non-object and non-boolean config never auto-enables or overwrites", async (t) => {
  const f = fixture(t);
  const inputs = ["{bad JSON", "null", "[]", "true", ...[null, 0, 1, "true", "false", [], {}].map((v) => JSON.stringify({ plugin_auto_update: v }))];
  for (const input of inputs) {
    fs.writeFileSync(f.configFile, input);
    const u = createUpdater(f.options); await u.tick();
    assert.equal(u.status().auto, false, input); assert.equal(f.calls.length, 0, input);
    assert.equal(fs.readFileSync(f.configFile, "utf8"), input);
  }
});
test("manual detection only refreshes this marketplace and never installs", async (t) => {
  const f = fixture(t); await f.updater.request({ action: "check" });
  assert.deepEqual(f.calls, [["marketplace", "update", "zusage-marketplace"], ["list", "--available"]]);
  assert.equal(f.updater.status().phase, "available"); assert.equal(f.syncs.length, 0);
  assert.ok(f.states.some((s) => s.phase === "checking" && s.busy));
});
test("manual update rechecks then uses official installer and synchronizes installed path", async (t) => {
  const f = fixture(t); f.set({ changed: ["overlay.js", "inject-main.cjs"], skipped: ["zusage.py"] });
  await f.updater.request({ action: "update", path: "/untrusted/script" });
  assert.deepEqual(f.calls[2], ["update", PLUGIN_ID]);
  assert.deepEqual(f.syncs, [f.record.installPath]);
  assert.equal(f.updater.status().phase, "updated"); assert.equal(f.updater.status().restartRequired, true);
  assert.deepEqual(f.updater.status().skipped, ["zusage.py"]);
});
test("up-to-date and older marketplace versions never install", async (t) => {
  const f = fixture(t); f.set({ version: "1.0.0" }); await f.updater.request({ action: "update" });
  f.set({ version: "0.9.0" }); await f.updater.request({ action: "update" });
  assert.equal(f.calls.filter((c) => c[0] === "update").length, 0); assert.equal(f.updater.status().phase, "current");
});
test("auto enable persists configuration and performs check plus install", async (t) => {
  const f = fixture(t); await f.updater.request({ action: "auto", enabled: true });
  const cfg = JSON.parse(fs.readFileSync(f.configFile));
  assert.equal(cfg.plugin_auto_update, true); assert.equal(cfg.lang, "en"); assert.deepEqual(cfg.remote, { enabled: false });
  assert.equal(f.updater.status().phase, "updated");
  assert.ok(f.calls.some((c) => c[0] === "update"));
});
test("auto checks are throttled across ticks and loader restarts", async (t) => {
  const f = fixture(t, { plugin_auto_update: true }); await f.updater.tick();
  const count = f.calls.length; await f.updater.tick(); assert.equal(f.calls.length, count);
  const restored = createUpdater(f.options); await restored.tick(); assert.equal(f.calls.length, count);
  f.advance(INTERVAL_MS); await restored.tick(); assert.equal(f.calls.length, count + 2);
});
test("auto disable stops future checks while manual check remains usable", async (t) => {
  const f = fixture(t, { plugin_auto_update: true }); await f.updater.request({ action: "auto", enabled: false });
  await f.updater.tick(); assert.equal(f.calls.length, 0);
  await f.updater.request({ action: "check" }); assert.equal(f.calls.length, 2);
});
test("auto never updates a disabled plugin", async (t) => {
  const f = fixture(t, { plugin_auto_update: true }); f.set({ enabled: false }); await f.updater.tick();
  assert.equal(f.updater.status().phase, "available"); assert.equal(f.calls.length, 2);
});
test("missing manifest version or entry is an error, not up-to-date", async (t) => {
  const f = fixture(t); f.set({ version: "" }); await f.updater.request({ action: "check" });
  assert.equal(f.updater.status().phase, "error"); assert.equal(f.updater.status().checkedAt, 0);
  f.set({ missing: true }); await f.updater.request({ action: "check" }); assert.equal(f.updater.status().phase, "error");
});
test("network failure is visible and automatic retries are throttled", async (t) => {
  const f = fixture(t, { plugin_auto_update: true }); f.set({ error: "network unavailable" }); await f.updater.tick();
  assert.equal(f.updater.status().phase, "error"); assert.match(f.updater.status().error, /network unavailable/);
  await f.updater.tick(); assert.equal(f.calls.length, 1);
  f.set({ error: null }); await f.updater.request({ action: "check" }); assert.equal(f.updater.status().phase, "available");
});
test("CLI error diagnostics are honored even on exit zero", async (t) => {
  const f = fixture(t); const u = createUpdater({ ...f.options,
    cli: async () => ({ diagnostics: [{ severity: "error", message: "market missing" }] }) });
  await u.request({ action: "check" }); assert.equal(u.status().phase, "error"); assert.equal(u.status().error, "market missing");
});
test("invalid config is never overwritten", async (t) => {
  const f = fixture(t); fs.writeFileSync(f.configFile, "{bad JSON");
  await f.updater.request({ action: "auto", enabled: true });
  assert.equal(fs.readFileSync(f.configFile, "utf8"), "{bad JSON"); assert.equal(f.updater.status().phase, "error");
});
test("uninstalled plugin or unavailable host bundle cannot update", async (t) => {
  const f = fixture(t); fs.writeFileSync(f.recordFile, '{"plugins":[]}'); await f.updater.request({ action: "check" });
  assert.equal(f.updater.status().supported, false); assert.equal(f.calls.length, 0);
  f.writeRecord(); const u = createUpdater({ ...f.options, cli: undefined }); assert.equal(u.status().supported, false);
});
test("missing config is created without requiring installer-generated settings", async (t) => {
  const f = fixture(t); fs.unlinkSync(f.configFile);
  await f.updater.request({ action: "auto", enabled: true });
  assert.deepEqual(JSON.parse(fs.readFileSync(f.configFile)), { plugin_auto_update: true });
  assert.equal(f.updater.status().phase, "updated");
});
test("failed runtime sync can be retried after restart without reinstalling plugin", async (t) => {
  const f = fixture(t); let fail = true;
  const options = { ...f.options, sync: async () => { if (fail) throw new Error("runtime locked"); return { changed: ["overlay.js"] }; } };
  const u = createUpdater(options); await u.request({ action: "update" });
  assert.equal(u.status().phase, "error"); assert.equal(u.status().pendingSync, true);
  assert.equal(f.record.version, "1.1.0");
  fail = false;
  const restored = createUpdater(options); await restored.request({ action: "check" });
  assert.equal(restored.status().phase, "updated"); assert.equal(restored.status().pendingSync, false);
  assert.equal(f.calls.filter((c) => c[0] === "update").length, 1);
});
test("pending runtime synchronization recovers offline without waiting 24 hours", async (t) => {
  const f = fixture(t, { plugin_auto_update: true });
  fs.writeFileSync(path.join(f.runtime, "plugin-update.json"), JSON.stringify({ lastAttempt: f.options.now(), pendingSync: true }));
  f.set({ error: "network unavailable" });
  const u = createUpdater(f.options); await u.tick();
  assert.equal(u.status().phase, "updated"); assert.equal(u.status().pendingSync, false);
  assert.equal(f.calls.length, 0); assert.equal(f.syncs.length, 1);
});
test("multiple hook and loader instances serialize updates across the shared runtime", async (t) => {
  const f = fixture(t, { plugin_auto_update: true }); let release, entered;
  const blocked = new Promise((resolve) => { release = resolve; });
  const started = new Promise((resolve) => { entered = resolve; });
  const options = { ...f.options, cli: async (args) => { entered(); await blocked;
    if (args[0] === "marketplace") return { diagnostics: [] };
    return { installed: [{ id: PLUGIN_ID, version: "1.0.0", enabled: true }], available: [{ id: PLUGIN_ID, version: "1.0.0" }] }; } };
  const first = createUpdater(options), second = createUpdater(options);
  const running = first.tick(); await started; await second.tick();
  assert.equal(first.status().busy, true); assert.equal(second.status().busy, false);
  release(); await running; assert.equal(fs.existsSync(path.join(f.runtime, "plugin-update-v2.lock")), true);
  await second.tick(); assert.equal(second.status().lastAttempt, first.status().lastAttempt);
});
test("lock owned by a live process prevents updates and lock failures are visible", async (t) => {
  const f = fixture(t); const lock = path.join(f.runtime, "plugin-update.lock");
  fs.writeFileSync(lock, JSON.stringify({ pid: process.pid }));
  await f.updater.request({ action: "update" }); assert.equal(f.calls.length, 0);
  fs.unlinkSync(lock); fs.mkdirSync(path.join(f.runtime, "plugin-update-v2.lock"));
  await f.updater.request({ action: "check" }); assert.equal(f.updater.status().phase, "error");
});
test("unknown actions and invalid settings have no effects", async (t) => {
  const f = fixture(t); await f.updater.request({ action: "shell", command: "evil" });
  await f.updater.request({ action: "auto", enabled: "true" }); assert.equal(f.calls.length, 0);
});
test("busy operations ignore duplicate update requests", async (t) => {
  const f = fixture(t); let release;
  const blocked = new Promise((resolve) => { release = resolve; });
  const u = createUpdater({ ...f.options, cli: async () => { await blocked; return { installed: [], available: [], diagnostics: [] }; } });
  const pending = u.request({ action: "check" });
  await new Promise((resolve) => setImmediate(resolve));
  await u.request({ action: "update" }); release(); await pending; assert.equal(f.syncs.length, 0);
});

test("invalid state objects and fields recover without changing automatic opt-out", async (t) => {
  const f = fixture(t, { plugin_auto_update: false });
  const stateFile = path.join(f.runtime, "plugin-update.json");
  for (const input of ["null", "[]", "true", "{bad", JSON.stringify({ checkedAt: "bad", lastAttempt: -1, pendingSync: "true" }),
    JSON.stringify({ checkedAt: 1e30, lastAttempt: "1", pendingSync: false }),
    JSON.stringify({ checkedAt: Number.MAX_SAFE_INTEGER, lastAttempt: Number.MAX_SAFE_INTEGER })]) {
    fs.writeFileSync(stateFile, input);
    const u = createUpdater(f.options); await u.tick();
    assert.equal(u.status().auto, false); assert.equal(u.status().checkedAt, 0);
    assert.equal(u.status().lastAttempt, 0); assert.equal(u.status().pendingSync, false);
  }
  assert.equal(f.calls.length, 0);
});
test("automatic install re-reads cross-instance opt-out after marketplace check", async (t) => {
  const f = fixture(t); const original = f.options.cli;
  const u = createUpdater({ ...f.options, cli: async (args) => {
    const result = await original(args);
    if (args[0] === "list") fs.writeFileSync(f.configFile, JSON.stringify({ plugin_auto_update: false }));
    return result;
  } });
  await u.tick(); assert.equal(u.status().auto, false);
  assert.equal(f.calls.filter((c) => c[0] === "update").length, 0);
});
test("disable is persisted while a check is busy and prevents automatic install", async (t) => {
  const f = fixture(t); let resume, entered;
  const gate = new Promise((resolve) => { resume = resolve; });
  const started = new Promise((resolve) => { entered = resolve; });
  const u = createUpdater({ ...f.options, cli: async (args) => { entered(); await gate; return f.options.cli(args); } });
  const running = u.tick(); await started;
  await u.request({ action: "auto", enabled: false });
  assert.equal(JSON.parse(fs.readFileSync(f.configFile)).plugin_auto_update, false);
  resume(); await running; assert.equal(f.calls.filter((c) => c[0] === "update").length, 0);
});
test("pending intent exists before installer commits and failed committed CLI recovers offline", async (t) => {
  const f = fixture(t); const stateFile = path.join(f.runtime, "plugin-update.json");
  const u = createUpdater({ ...f.options, cli: async (args) => {
    if (args[0] !== "update") return f.options.cli(args);
    const intent = JSON.parse(fs.readFileSync(stateFile));
    assert.equal(intent.pendingSync, true); assert.equal(intent.pendingVersion, "1.0.0");
    await f.options.cli(args); throw new Error("CLI failed after inventory commit");
  } });
  await u.request({ action: "update" });
  assert.equal(u.status().phase, "updated"); assert.equal(f.syncs.length, 1);
  assert.equal(JSON.parse(fs.readFileSync(stateFile)).pendingSync, false);
});
test("persistent restart marker survives updater restart and throttled background tick", async (t) => {
  const f = fixture(t); f.set({ changed: ["plugin-updater.cjs"] });
  await f.updater.request({ action: "update" });
  assert.equal(fs.existsSync(path.join(f.runtime, ".plugin-restart-required")), true);
  const u = createUpdater(f.options); await u.tick();
  assert.equal(u.status().restartRequired, true);
});
test("uncommitted installer failures are errors, never sync, and permit next automatic retry", async (t) => {
  const f = fixture(t); let attempts = 0;
  const u = createUpdater({ ...f.options, cli: async (args) => {
    if (args[0] !== "update") return f.options.cli(args);
    attempts++; throw new Error("install failed before commit");
  } });
  await u.tick(); assert.equal(u.status().phase, "error"); assert.equal(u.status().pendingSync, false);
  assert.equal(u.status().lastAttempt, 0); assert.equal(f.syncs.length, 0);
  await u.tick(); assert.equal(attempts, 2); assert.equal(f.syncs.length, 0);
});
test("CLI success without a newer official inventory commit is not successful update", async (t) => {
  const f = fixture(t);
  const u = createUpdater({ ...f.options, cli: async (args) => args[0] === "update" ? { ok: true } : f.options.cli(args) });
  await u.tick(); assert.equal(u.status().phase, "error"); assert.match(u.status().error, /未提交/);
  assert.equal(f.syncs.length, 0); assert.equal(u.status().pendingSync, false);
});
test("persisted pre-install intent recovers committed version offline even after auto disabled", async (t) => {
  const f = fixture(t, { plugin_auto_update: false }); f.record.version = "1.1.0"; f.writeRecord();
  fs.writeFileSync(path.join(f.runtime, "plugin-update.json"), JSON.stringify({
    lastAttempt: f.options.now(), pendingSync: true, pendingVersion: "1.0.0",
  }));
  f.set({ error: "offline" }); const u = createUpdater(f.options); await u.tick();
  assert.equal(u.status().phase, "updated"); assert.equal(f.calls.length, 0); assert.equal(f.syncs.length, 1);
  assert.equal(u.status().auto, false); assert.equal(u.status().pendingSync, false);
});
test("uncommitted intent with auto disabled clears without networking or claiming update", async (t) => {
  const f = fixture(t, { plugin_auto_update: false });
  fs.writeFileSync(path.join(f.runtime, "plugin-update.json"), JSON.stringify({
    lastAttempt: f.options.now(), pendingSync: true, pendingVersion: "1.0.0",
  }));
  const u = createUpdater(f.options); await u.tick();
  assert.equal(u.status().phase, "idle"); assert.equal(u.status().pendingSync, false); assert.equal(u.status().lastAttempt, 0);
  assert.equal(f.calls.length, 0); assert.equal(f.syncs.length, 0);
});
test("disable after installer start saves opt-out without cancelling already-launched commit", async (t) => {
  const f = fixture(t); let resume, entered;
  const gate = new Promise((resolve) => { resume = resolve; });
  const started = new Promise((resolve) => { entered = resolve; });
  const u = createUpdater({ ...f.options, cli: async (args) => {
    if (args[0] === "update") { entered(); await gate; }
    return f.options.cli(args);
  } });
  const running = u.tick(); await started;
  await u.request({ action: "auto", enabled: false }); resume(); await running;
  assert.equal(u.status().phase, "updated"); assert.equal(u.status().auto, false);
  assert.equal(f.calls.filter((c) => c[0] === "update").length, 1);
  assert.equal(JSON.parse(fs.readFileSync(f.configFile)).plugin_auto_update, false);
});
test("hot-reload opt-out marks changed overlay or usage only, not unchanged or tools", async (t) => {
  for (const changed of [[], ["usage_mcp.py"], ["overlay.js"], ["zusage.py"]]) {
    const f = fixture(t, { hot_reload: false }); f.set({ changed });
    await f.updater.tick(); assert.equal(f.updater.status().restartRequired, changed.includes("overlay.js") || changed.includes("zusage.py"));
  }
});
test("current sync report marker is not re-recorded by legacy fallback", async (t) => {
  const f = fixture(t); const marker = path.join(f.runtime, ".plugin-restart-required");
  const u = createUpdater({ ...f.options, sync: async () => {
    fs.writeFileSync(marker, JSON.stringify({ createdAt: 1, hashes: { "plugin-updater.cjs": "saved" } }));
    return { changed: ["plugin-updater.cjs"], restartRecorded: true };
  } });
  await u.tick(); assert.equal(JSON.parse(fs.readFileSync(marker)).createdAt, 1);
  assert.equal(u.status().restartRequired, true);
});
test("marker write failure leaves pending intent for retry and never clears reminder", async (t) => {
  const f = fixture(t); const marker = path.join(f.runtime, ".plugin-restart-required");
  fs.mkdirSync(marker); f.set({ changed: ["inject-main.cjs"] });
  await f.updater.tick(); assert.equal(f.updater.status().phase, "error"); assert.equal(f.updater.status().pendingSync, true);
  assert.equal(JSON.parse(fs.readFileSync(path.join(f.runtime, "plugin-update.json"))).pendingVersion, "1.0.0");
  fs.rmdirSync(marker); const restored = createUpdater(f.options); await restored.tick();
  assert.equal(restored.status().phase, "updated"); assert.equal(restored.status().restartRequired, true);
  assert.equal(f.calls.filter((c) => c[0] === "update").length, 1);
});
test("invalid sync JSON report keeps coordination pending", async (t) => {
  const f = fixture(t); const u = createUpdater({ ...f.options, sync: async () => null });
  await u.tick(); assert.equal(u.status().phase, "error"); assert.equal(u.status().pendingSync, true);
});
test("null or damaged inventory cannot crash updater or trigger official install", async (t) => {
  const f = fixture(t);
  for (const input of ["null", "[]", "{bad", '{"plugins":[null]}']) {
    fs.writeFileSync(f.recordFile, input); const u = createUpdater(f.options); await u.tick();
    assert.equal(u.status().supported, false); assert.equal(f.calls.length, 0);
  }
});
test("dead legacy wx lock is ignored without deletion and stale file remains", async (t) => {
  const f = fixture(t); const old = path.join(f.runtime, "plugin-update.lock");
  fs.writeFileSync(old, JSON.stringify({ pid: 2147483647, createdAt: 1 }));
  await f.updater.tick(); assert.equal(f.updater.status().phase, "updated");
  assert.equal(fs.existsSync(old), true); assert.equal(fs.existsSync(path.join(f.runtime, "plugin-update-v2.lock")), true);
});
test("missing Python helper errors are visible and do not leave updater busy", async (t) => {
  const f = fixture(t, { python_path: path.join(os.tmpdir(), "missing-zusage-python") });
  await f.updater.tick(); assert.equal(f.updater.status().phase, "error"); assert.equal(f.updater.status().busy, false);
  assert.equal(f.calls.length, 0);
});
