"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const path = require("path");
const os = require("os");
const vm = require("vm");
const crypto = require("crypto");
const { execFile } = require("child_process");

async function bootLoader(t, markerHashes, createdAt) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "zusage-restart-boot-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const files = { "inject-main.cjs": "/* current loader */", "plugin-updater.cjs": "/* current updater */",
    "overlay.js": "/* current overlay */", "zusage.py": "# current query" };
  const hashes = {};
  for (const [name, body] of Object.entries(files)) {
    fs.writeFileSync(path.join(root, name), body);
    hashes[name] = crypto.createHash("sha256").update(body).digest("hex");
  }
  fs.copyFileSync(path.join(__dirname, "..", "runtime_lock.py"), path.join(root, "runtime_lock.py"));
  fs.writeFileSync(path.join(root, "config.json"), JSON.stringify({ python_path: process.env.ZUSAGE_TEST_PYTHON || "python3" }));
  const marker = path.join(root, ".plugin-restart-required");
  fs.writeFileSync(marker, JSON.stringify({ createdAt: createdAt || Date.now() / 1000 - 1,
    hashes: markerHashes ? markerHashes(hashes) : hashes }));
  let complete;
  const done = new Promise((resolve, reject) => { complete = { resolve, reject }; });
  const source = fs.readFileSync(path.join(__dirname, "..", "inject-main.cjs"), "utf8");
  const prefix = source.slice(0, source.indexOf("/* config.json 内容缓存"));
  vm.runInNewContext(prefix + "\nacknowledgeRestart();", {
    __dirname: root, console: { error: () => {} }, process: { platform: process.platform, uptime: () => 0 },
    require: (id) => {
      if (id === "electron") return {};
      if (id === "child_process") return { execFile: (command, args, options, callback) => {
        execFile(command, args, options, (error, stdout, stderr) => {
          callback(error, stdout, stderr);
          if (error) complete.reject(error); else complete.resolve();
        });
      } };
      return require(id);
    },
  });
  await done;
  return { root, marker, hashes };
}

test("real startup fingerprint acknowledgement clears only deployed matching hashes", async (t) => {
  const boot = await bootLoader(t);
  assert.equal(fs.existsSync(boot.marker), false);
});

test("real startup retains newer pending file while removing matching older entries", async (t) => {
  const boot = await bootLoader(t, (hashes) => ({ ...hashes, "plugin-updater.cjs": "f".repeat(64) }));
  assert.deepEqual(JSON.parse(fs.readFileSync(boot.marker, "utf8")).hashes,
    { "plugin-updater.cjs": "f".repeat(64) });
});

test("restart notification written after startup snapshot is not cleared even if hashes match", async (t) => {
  const boot = await bootLoader(t, null, Date.now() / 1000 + 60);
  assert.ok(fs.existsSync(boot.marker));
  assert.deepEqual(JSON.parse(fs.readFileSync(boot.marker, "utf8")).hashes, boot.hashes);
});
