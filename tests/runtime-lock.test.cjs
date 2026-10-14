"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const os = require("os");
const path = require("path");
const readline = require("readline");
const { spawn, execFile } = require("child_process");
const { promisify } = require("util");
const execute = promisify(execFile);
const { createUpdater, PLUGIN_ID } = require("../plugin-updater.cjs");
const helper = path.resolve(__dirname, "../runtime_lock.py");
const python = process.env.ZUSAGE_PLUGIN_PYTHON || (process.platform === "win32" ? "python" : "python3");
const env = { ...process.env, PYTHONDONTWRITEBYTECODE: "1" };

function fixture(t) {
  const runtime = fs.mkdtempSync(path.join(os.tmpdir(), "zusage-lock-test-"));
  const children = [];
  t.after(() => {
    for (const child of children) if (child.exitCode === null && child.signalCode === null) child.kill();
    fs.rmSync(runtime, { recursive: true, force: true });
  });
  const start = (command, args) => {
    const child = spawn(command, args, { windowsHide: true, env, stdio: ["pipe", "pipe", "pipe"] });
    children.push(child); child.stdin.on("error", () => {});
    return child;
  };
  return { runtime, lock: path.join(runtime, "plugin-update-v2.lock"), start };
}
function line(child) {
  return new Promise((resolve, reject) => {
    const reader = readline.createInterface({ input: child.stdout });
    const timeout = setTimeout(() => finish(new Error("child did not become ready")), 5000);
    const finish = (error, text) => {
      clearTimeout(timeout); reader.close(); child.removeListener("error", onError); child.removeListener("exit", onExit);
      error ? reject(error) : resolve(text);
    };
    const onError = (error) => finish(error);
    const onExit = () => finish(new Error("child exited before readiness"));
    child.once("error", onError); child.once("exit", onExit);
    reader.once("line", (text) => finish(null, text));
  });
}
function exited(child) {
  if (child.exitCode !== null || child.signalCode !== null) return Promise.resolve();
  return new Promise((resolve) => child.once("close", resolve));
}
async function hold(f) {
  const child = f.start(python, [helper, "--hold", f.lock]);
  const data = JSON.parse(await line(child));
  return { child, data };
}
async function markerCommand(runtime, action, data) {
  const args = [helper, action, runtime];
  if (action === "--ack-restart") args.push(JSON.stringify(data));
  const child = spawn(python, args, { windowsHide: true });
  const ready = line(child);
  child.stdin.on("error", () => {});
  child.stdin.end(action === "--record-restart" ? JSON.stringify(data) : undefined);
  const result = JSON.parse(await ready); await exited(child); return result;
}

test("orphaned official installer keeps recovery waiting until actual child exit", async (t) => {
  const f = fixture(t); const home = path.join(f.runtime, "home");
  const inventory = path.join(home, ".zcode", "cli", "plugins", "installed_plugins.json");
  const bundle = path.join(f.runtime, "glm", "zcode.cjs");
  const ready = path.join(f.runtime, "installer-ready.json"); const gate = path.join(f.runtime, "installer-release");
  fs.mkdirSync(path.dirname(inventory), { recursive: true }); fs.mkdirSync(path.dirname(bundle), { recursive: true });
  const record = { id: PLUGIN_ID, scope: "user", version: "1.0.0", installPath: path.join(f.runtime, "installed") };
  fs.writeFileSync(inventory, JSON.stringify({ plugins: [record] }));
  fs.writeFileSync(path.join(path.dirname(inventory), "known_marketplaces.json"), JSON.stringify({ marketplaces: [
    { id: "zusage-marketplace", source: { source: "directory", path: home } },
  ] }));
  fs.writeFileSync(path.join(f.runtime, "config.json"), JSON.stringify({ python_path: python }));
  fs.writeFileSync(bundle, `
    const fs=require('fs'); const inventory=${JSON.stringify(inventory)};
    const args=process.argv.slice(3);
    if(args[0]==='marketplace') process.stdout.write(JSON.stringify({diagnostics:[]}));
    else if(args[0]==='list') process.stdout.write(JSON.stringify({installed:[{id:${JSON.stringify(PLUGIN_ID)},version:'1.0.0'}],available:[{id:${JSON.stringify(PLUGIN_ID)},version:'1.1.0'}]}));
    else {
      const data=JSON.parse(fs.readFileSync(inventory)); data.plugins[0].version='1.1.0'; fs.writeFileSync(inventory,JSON.stringify(data));
      fs.writeFileSync(${JSON.stringify(ready)},JSON.stringify({pid:process.pid}));
      const timer=setInterval(()=>{if(fs.existsSync(${JSON.stringify(gate)})){clearInterval(timer); process.stdout.write(JSON.stringify({ok:true}));}},25);
    }
  `);
  const parent = f.start(process.execPath, ["-e", `
    const {createUpdater}=require(${JSON.stringify(path.resolve(__dirname, "../plugin-updater.cjs"))});
    createUpdater({runtime:${JSON.stringify(f.runtime)},home:${JSON.stringify(home)},resourcesPath:${JSON.stringify(f.runtime)},executable:process.execPath,sync:async()=>({})}).tick();
  `]);
  for (let attempt = 0; attempt < 200 && !fs.existsSync(ready); attempt++) await new Promise((resolve) => setTimeout(resolve, 10));
  assert.equal(fs.existsSync(ready), true);
  const pid = JSON.parse(fs.readFileSync(ready)).pid;
  t.after(() => { try { process.kill(pid, "SIGKILL"); } catch (e) {} });
  const persisted = JSON.parse(fs.readFileSync(path.join(f.runtime, "plugin-update.json")));
  assert.equal(persisted.pendingPid, pid); assert.equal(persisted.pendingVersion, "1.0.0");
  parent.kill("SIGKILL"); await exited(parent);
  let calls = 0, syncs = 0;
  const updater = createUpdater({ runtime: f.runtime, home, resourcesPath: f.runtime, executable: process.execPath,
    cli: async () => { calls++; throw new Error("must not dispatch"); }, sync: async () => { syncs++; return {}; } });
  await updater.tick(); assert.equal(updater.status().phase, "updating"); assert.equal(updater.status().pendingSync, true);
  assert.equal(calls, 0); assert.equal(syncs, 0);
  fs.writeFileSync(gate, "release");
  for (let attempt = 0; attempt < 200; attempt++) {
    try { process.kill(pid, 0); } catch (e) { if (e.code === "ESRCH") break; }
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  await updater.tick(); assert.equal(updater.status().phase, "updated"); assert.equal(updater.status().pendingSync, false);
  assert.equal(calls, 0); assert.equal(syncs, 1);
});

test("OS lock is non-blocking, EOF releases, and permanent inode is reused", async (t) => {
  const f = fixture(t); const first = await hold(f);
  assert.deepEqual(first.data, { locked: true });
  const inode = fs.statSync(f.lock).ino;
  const blocked = await hold(f); assert.deepEqual(blocked.data, { locked: false }); await exited(blocked.child);
  first.child.stdin.end(); await exited(first.child);
  const next = await hold(f); assert.deepEqual(next.data, { locked: true });
  assert.equal(fs.statSync(f.lock).ino, inode);
  next.child.stdin.end(); await exited(next.child);
  assert.equal(fs.existsSync(f.lock), true);
});
test("killed holder releases OS lock without stale-owner unlink", async (t) => {
  const f = fixture(t); const first = await hold(f); assert.equal(first.data.locked, true);
  first.child.kill("SIGKILL"); await exited(first.child);
  const next = await hold(f); assert.equal(next.data.locked, true);
  next.child.stdin.end(); await exited(next.child);
});
test("parent death closes helper stdin and releases the lock", async (t) => {
  const f = fixture(t);
  const parent = f.start(process.execPath, ["-e", `
    const {spawn}=require('child_process');
    const child=spawn(${JSON.stringify(python)},[${JSON.stringify(helper)},'--hold',${JSON.stringify(f.lock)}],{stdio:['pipe','pipe','pipe']});
    child.stdout.pipe(process.stdout);
    setInterval(()=>{},1000);
  `]);
  assert.deepEqual(JSON.parse(await line(parent)), { locked: true });
  parent.kill("SIGKILL"); await exited(parent);
  let next;
  for (let attempt = 0; attempt < 20; attempt++) {
    next = await hold(f);
    if (next.data.locked) break;
    await exited(next.child); await new Promise((resolve) => setTimeout(resolve, 25));
  }
  assert.equal(next.data.locked, true); next.child.stdin.end(); await exited(next.child);
});
test("multiple real holder processes have exactly one winner and no unlink race", async (t) => {
  const f = fixture(t); const holders = await Promise.all(Array.from({ length: 12 }, () => hold(f)));
  const winners = holders.filter((h) => h.data.locked); assert.equal(winners.length, 1);
  for (const h of holders) if (!h.data.locked) await exited(h.child);
  winners[0].child.stdin.end(); await exited(winners[0].child);
  const next = await hold(f); assert.equal(next.data.locked, true);
  next.child.stdin.end(); await exited(next.child);
});
test("Python context manager times out then recovers after exceptions", async (t) => {
  const f = fixture(t); const first = await hold(f);
  const code = `import sys,time\nsys.path.insert(0,${JSON.stringify(path.dirname(helper))})\nfrom runtime_lock import file_lock\nstart=time.monotonic()\ntry:\n with file_lock(${JSON.stringify(f.lock)},timeout=0.1): pass\nexcept TimeoutError:\n print(time.monotonic()-start)\n`;
  const result = await execute(python, ["-c", code], { env });
  assert.ok(Number(result.stdout) >= 0.09); assert.ok(Number(result.stdout) < 2);
  first.child.stdin.end(); await exited(first.child);
  const recovery = `import sys\nsys.path.insert(0,${JSON.stringify(path.dirname(helper))})\nfrom runtime_lock import file_lock\ntry:\n with file_lock(${JSON.stringify(f.lock)},timeout=0): raise ValueError('test')\nexcept ValueError: pass\nwith file_lock(${JSON.stringify(f.lock)},timeout=0): print('recovered')\n`;
  assert.equal((await execute(python, ["-c", recovery], { env })).stdout.trim(), "recovered");
});
test("marker merge and acknowledgement remove only loaded hashes", async (t) => {
  const f = fixture(t); const marker = path.join(f.runtime, ".plugin-restart-required");
  await markerCommand(f.runtime, "--record-restart", { hashes: { "inject-main.cjs": "old", "plugin-updater.cjs": "same" } });
  await markerCommand(f.runtime, "--record-restart", { hashes: { "inject-main.cjs": "new" } });
  let data = JSON.parse(fs.readFileSync(marker));
  assert.deepEqual(data.hashes, { "inject-main.cjs": "new", "plugin-updater.cjs": "same" });
  assert.equal((await markerCommand(f.runtime, "--ack-restart", { "inject-main.cjs": "old", "plugin-updater.cjs": "same" })).cleared, false);
  data = JSON.parse(fs.readFileSync(marker)); assert.deepEqual(data.hashes, { "inject-main.cjs": "new" });
  assert.equal((await markerCommand(f.runtime, "--ack-restart", { "inject-main.cjs": "new" })).cleared, true);
  assert.equal(fs.existsSync(marker), false); assert.equal(fs.existsSync(path.join(f.runtime, "plugin-restart.lock")), true);
});
test("startup snapshot cannot acknowledge a newer marker even with matching hashes", async (t) => {
  const f = fixture(t); const loadedAt = Date.now() - 1000;
  await markerCommand(f.runtime, "--record-restart", { hashes: { "inject-main.cjs": "same" } });
  assert.equal((await markerCommand(f.runtime, "--ack-restart", { _loadedAt: loadedAt, "inject-main.cjs": "same" })).cleared, false);
  assert.equal((await markerCommand(f.runtime, "--ack-restart", { _loadedAt: Date.now() + 1000, "inject-main.cjs": "same" })).cleared, true);
});
test("legacy marker obeys startup time boundary and supports old acknowledgement", async (t) => {
  const f = fixture(t);
  await markerCommand(f.runtime, "--record-restart", {});
  assert.equal((await markerCommand(f.runtime, "--ack-restart", { _loadedAt: Date.now() - 1000 })).cleared, false);
  assert.equal((await markerCommand(f.runtime, "--ack-restart", {})).cleared, true);
});
test("concurrent record and stale ack never lose an unmatched notification", async (t) => {
  const f = fixture(t); const marker = path.join(f.runtime, ".plugin-restart-required");
  await markerCommand(f.runtime, "--record-restart", { hashes: { "inject-main.cjs": "old" } });
  await Promise.all([
    markerCommand(f.runtime, "--record-restart", { hashes: { "inject-main.cjs": "new" } }),
    markerCommand(f.runtime, "--ack-restart", { "inject-main.cjs": "old" }),
  ]);
  assert.equal(JSON.parse(fs.readFileSync(marker)).hashes["inject-main.cjs"], "new");
});
