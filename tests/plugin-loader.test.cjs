"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const { pathToFileURL } = require("url");

test("loader consumes only fixed update actions from trusted local windows and pushes state", async () => {
  const source = fs.readFileSync(path.join(__dirname, "..", "inject-main.cjs"), "utf8");
  const resources = path.resolve("/fixture/resources");
  const intervals = [], requests = [], scripts = [], windows = [], acknowledgements = [];
  const status = { supported: true, phase: "available", auto: false };
  let tickCount = 0;
  const stub = { request: async (r) => requests.push(r), tick: async () => { tickCount++; }, status: () => status };
  const makeWindow = (url, queue) => ({
    getType: () => "window", isDestroyed: () => false, getURL: () => url, isLoading: () => false,
    on: () => {}, executeJavaScript: async (script) => {
      scripts.push(script);
      return script.includes("q.splice") ? queue.splice(0, 8) : "[]";
    },
  });
  windows.push(makeWindow(pathToFileURL(path.join(resources, "app.asar", "out", "renderer", "index.html")).href + "?restore", [
    { action: "check" }, { action: "shell", command: "ignored" }, { action: "auto", enabled: true },
  ]));
  windows.push(makeWindow("https://example.com", [{ action: "update" }]));
  windows.push(makeWindow(pathToFileURL(path.join(resources, "..", "outside.html")).href, [{ action: "update" }]));
  const mockFs = { readFileSync: (file) => String(file).endsWith("overlay.js") ? "/* overlay */" : "{}",
    statSync: () => ({ mtimeMs: 1 }), existsSync: () => true, watch: () => ({ on: () => {} }), writeFileSync: () => {},
    unlinkSync: () => { throw new Error("Loader must acknowledge instead of deleting the marker directly"); } };
  const sandbox = {
    __dirname: "/fixture/runtime", console: { error: () => {} },
    process: { resourcesPath: resources, execPath: process.execPath, uptime: () => 1 },
    setInterval: (fn, delay) => { intervals.push({ fn, delay }); },
    setTimeout: () => 1, clearTimeout: () => {},
    require: (id) => {
      if (id === "path") return path;
      if (id === "fs") return mockFs;
      if (id === "url") return require("url");
      if (id === "crypto") return require("crypto");
      if (id === "child_process") return { spawn: () => { throw new Error("Query must not run"); },
        execFile: (command, args, options, callback) => { acknowledgements.push({ command, args, options }); callback(null); } };
      if (id === "./plugin-updater.cjs") return { createUpdater: (opts) => { opts.notify(status); return stub; } };
      if (id === "electron") return { app: { getPath: () => "/fixture/home", on: () => {} },
        webContents: { getAllWebContents: () => windows }, ipcMain: { on: () => {} } };
      throw new Error(id);
    },
  };
  vm.runInNewContext(source, sandbox);
  await new Promise((r) => setImmediate(r));
  const poll = intervals.find((i) => i.delay === 1000);
  assert.ok(poll); await poll.fn();
  assert.equal(acknowledgements.length, 1);
  const ack = acknowledgements[0];
  assert.deepEqual(Array.from(ack.args.slice(0, 3)), ["/fixture/runtime/runtime_lock.py", "--ack-restart", "/fixture/runtime"]);
  const loaded = JSON.parse(ack.args[3]);
  assert.ok(loaded._loadedAt > 0);
  for (const name of ["inject-main.cjs", "plugin-updater.cjs", "overlay.js", "zusage.py"]) assert.match(loaded[name], /^[a-f0-9]{64}$/);
  assert.equal(ack.options.windowsHide, true);
  assert.deepEqual(requests.map((r) => r.action), ["check", "auto"]);
  assert.ok(scripts.some((s) => s.includes("window.__zusagePluginUpdate")));
  assert.equal(tickCount, 1);
  await intervals.find((i) => i.delay === 60000).fn(); assert.equal(tickCount, 2);
});

test("loader keeps polling controls while an update action is still awaiting the CLI", async () => {
  const source = fs.readFileSync(path.join(__dirname, "..", "inject-main.cjs"), "utf8");
  const actionSource = source.slice(source.indexOf("async function pollPluginRequests()"), source.indexOf("setInterval(pollPluginRequests"));
  const pending = new Promise(() => {});
  const requests = [], queue = [{ action: "update" }];
  const wc = { id: 1, isDestroyed: () => false, getType: () => "window",
    getURL: () => pathToFileURL("/fixture/resources/app.asar/out/renderer/index.html").href,
    executeJavaScript: async () => queue.splice(0, 8) };
  const sandbox = { pluginRequestBusy: false, process: { resourcesPath: "/fixture/resources" }, path,
    fileURLToPath: require("url").fileURLToPath, isMainWindowState: () => true,
    webContents: { getAllWebContents: () => [wc] }, LOG: () => {}, setTimeout: () => 1,
    pluginUpdater: { request: (req) => { requests.push(req); return req.action === "update" ? pending : Promise.resolve(); } } };
  vm.runInNewContext(actionSource, sandbox);
  await sandbox.pollPluginRequests();
  queue.push({ action: "auto", enabled: false });
  await sandbox.pollPluginRequests();
  assert.deepEqual(requests.map((r) => r.action), ["update", "auto"]);
  assert.equal(requests[1].enabled, false);
  assert.equal(sandbox.pluginRequestBusy, false);
});
