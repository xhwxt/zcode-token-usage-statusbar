"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const path = require("path");
const os = require("os");
const { resolvePluginStorage } = require("../plugin-storage.cjs");

function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "zusage-storage-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  fs.mkdirSync(path.join(root, ".git"));
  const home = path.join(root, "project", "home");
  fs.mkdirSync(home, { recursive: true });
  const userConfig = path.join(home, ".zcode", "cli", "config.json");
  const write = (file, config) => {
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, JSON.stringify(config));
  };
  return { root, home, userConfig, write };
}
function plugins(home, directory) {
  const root = directory.startsWith("~/") ? path.join(home, directory.slice(2)) : path.resolve(home, directory);
  return path.join(root, ...(path.basename(root) === "cli" ? [] : ["cli"]), "plugins");
}

test("default storage uses the supplied HOME and does not create configuration or storage", (t) => {
  const f = fixture(t);
  assert.equal(resolvePluginStorage(f.home, {}), path.join(f.home, ".zcode", "cli", "plugins"));
  assert.equal(fs.existsSync(path.join(f.home, ".zcode")), false);
});
test("user, ancestor projects, same-directory config and environment follow official priority", (t) => {
  const f = fixture(t);
  const files = [f.userConfig, path.join(f.root, "zcode.json"), path.join(f.root, ".zcode", "config.json"),
    path.join(f.root, "project", "zcode.json"), path.join(f.home, "zcode.json"), path.join(f.home, ".zcode", "config.json")];
  for (const [index, file] of files.entries()) {
    const dir = "store-" + index;
    f.write(file, { storage: { dir } });
    assert.equal(resolvePluginStorage(f.home, {}), plugins(f.home, dir));
  }
  assert.equal(resolvePluginStorage(f.home, { ZCODE_STORAGE_DIR: "environment" }), plugins(f.home, "environment"));
});
test("absolute, tilde and relative paths resolve from HOME without appending cli twice", (t) => {
  const f = fixture(t);
  const values = [path.join(f.root, "absolute"), path.join(f.root, "absolute", "cli"), "~/custom", "~/custom/cli",
    "relative", "../shared/cli/", ".", "~", " padded "];
  for (const dir of values) {
    f.write(f.userConfig, { storage: { dir } });
    const expected = plugins(f.home, dir);
    assert.equal(resolvePluginStorage(f.home, {}), expected);
    assert.equal(resolvePluginStorage(f.home, { ZCODE_STORAGE_DIR: dir }), expected);
    assert.ok(path.isAbsolute(expected));
  }
  f.write(f.userConfig, { storage: { sessionDbPath: path.join(f.root, "other.db") } });
  assert.equal(resolvePluginStorage(f.home, {}), plugins(f.home, "~/.zcode"));
});
test("nearest git directory or worktree file excludes configs above its boundary", (t) => {
  const f = fixture(t);
  f.write(path.join(f.root, "zcode.json"), { storage: { dir: null } });
  const boundary = path.join(f.root, "project", ".git");
  f.write(path.join(f.root, "project", "zcode.json"), { storage: { dir: "nearest" } });
  fs.mkdirSync(boundary);
  assert.equal(resolvePluginStorage(f.home, {}), plugins(f.home, "nearest"));
  fs.rmdirSync(boundary);
  fs.writeFileSync(boundary, "gitdir: fixture-only\n");
  assert.equal(resolvePluginStorage(f.home, {}), plugins(f.home, "nearest"));
});
test("without a git boundary only HOME project configs are read, never actual ancestor configs", (t) => {
  const f = fixture(t);
  f.write(path.join(f.root, "zcode.json"), { storage: { dir: null } });
  f.write(f.userConfig, { storage: { dir: "user" } });
  f.write(path.join(f.home, "zcode.json"), { storage: { dir: "home" } });
  const stat = fs.statSync;
  t.mock.method(fs, "statSync", (file, ...args) => {
    if (path.basename(file) === ".git") throw Object.assign(new Error("fixture has no boundary"), { code: "ENOENT" });
    return stat(file, ...args);
  });
  const read = fs.readFileSync;
  const expectedFiles = [f.userConfig, path.join(f.home, "zcode.json"), path.join(f.home, ".zcode", "config.json")];
  const reads = [];
  t.mock.method(fs, "readFileSync", (file, ...args) => {
    assert.ok(expectedFiles.includes(file), `must not read ancestor configuration: ${file}`);
    reads.push(file);
    return read(file, ...args);
  });
  assert.equal(resolvePluginStorage(f.home, {}), plugins(f.home, "home"));
  assert.deepEqual(reads, expectedFiles);
});
test("malformed JSON and invalid config objects fail closed even with a valid env override", (t) => {
  const f = fixture(t);
  for (const file of [f.userConfig, path.join(f.root, "zcode.json"), path.join(f.home, ".zcode", "config.json")]) {
    fs.mkdirSync(path.dirname(file), { recursive: true });
    for (const text of ["{broken", "null", "[]", "true", "123", '"config"']) {
      fs.writeFileSync(file, text);
      assert.throws(() => resolvePluginStorage(f.home, { ZCODE_STORAGE_DIR: "valid" }), /JSON 损坏|必须是对象/);
      assert.equal(fs.readFileSync(file, "utf8"), text);
    }
    fs.unlinkSync(file);
  }
});
test("storage and storage.dir types are strict, including overridden values", (t) => {
  const f = fixture(t);
  for (const storage of [null, [], true, 1, "directory"]) {
    f.write(f.userConfig, { storage });
    assert.throws(() => resolvePluginStorage(f.home, {}), /storage 必须是对象/);
  }
  for (const dir of [null, [], {}, true, 0, "", " \t\n"]) {
    f.write(f.userConfig, { storage: { dir } });
    assert.throws(() => resolvePluginStorage(f.home, { ZCODE_STORAGE_DIR: "valid" }), /storage.dir 必须是非空字符串/);
  }
  f.write(f.userConfig, { storage: {} });
  for (const value of [null, [], {}, true, 0, "", " \t\n"]) {
    assert.throws(() => resolvePluginStorage(f.home, { ZCODE_STORAGE_DIR: value }), /ZCODE_STORAGE_DIR 必须是非空字符串/);
  }
  assert.equal(resolvePluginStorage(f.home, { ZCODE_STORAGE_DIR: undefined }), plugins(f.home, "~/.zcode"));
});
test("config read errors and git boundary errors are not treated as missing settings", (t) => {
  const f = fixture(t);
  fs.mkdirSync(f.userConfig, { recursive: true });
  assert.throws(() => resolvePluginStorage(f.home, {}), /无法读取插件存储配置/);
  fs.rmdirSync(f.userConfig);
  const stat = fs.statSync;
  t.mock.method(fs, "statSync", (file, ...args) => {
    if (file === path.join(f.home, ".git")) throw Object.assign(new Error("fixture denied"), { code: "EACCES" });
    return stat(file, ...args);
  });
  assert.throws(() => resolvePluginStorage(f.home, {}), /无法识别.*Git 边界/);
});
test("repeated resolution observes config, env and git dependency changes and CLI root pin is idempotent", (t) => {
  const f = fixture(t), env = {};
  const original = resolvePluginStorage(f.home, env);
  f.write(f.userConfig, { storage: { dir: "user-store" } });
  assert.notEqual(resolvePluginStorage(f.home, env), original);
  const projectConfig = path.join(f.root, "zcode.json");
  f.write(projectConfig, { storage: { dir: "ancestor-store" } });
  assert.equal(resolvePluginStorage(f.home, env), plugins(f.home, "ancestor-store"));
  fs.mkdirSync(path.join(f.home, ".git"));
  assert.equal(resolvePluginStorage(f.home, env), plugins(f.home, "user-store"));
  const homeConfig = path.join(f.home, ".zcode", "config.json");
  f.write(homeConfig, { storage: { dir: "home-store" } });
  assert.equal(resolvePluginStorage(f.home, env), plugins(f.home, "home-store"));
  fs.unlinkSync(homeConfig);
  env.ZCODE_STORAGE_DIR = "environment-store";
  const selected = resolvePluginStorage(f.home, env);
  assert.equal(selected, plugins(f.home, "environment-store"));
  assert.equal(resolvePluginStorage(f.home, { ZCODE_STORAGE_DIR: path.dirname(selected) }), selected);
  delete env.ZCODE_STORAGE_DIR;
  fs.unlinkSync(f.userConfig);
  assert.equal(resolvePluginStorage(f.home, env), original);
});
test("default env parameter uses current process.env and does not infer beta storage", (t) => {
  const f = fixture(t), originalEnv = process.env;
  try {
    process.env = { ZCODE_STORAGE_DIR: "from-process", ZCODE_BETA: "1" };
    assert.equal(resolvePluginStorage(f.home), plugins(f.home, "from-process"));
    assert.equal(resolvePluginStorage(f.home, { ZCODE_BETA: "1", ZCODE_ENV: "beta" }), plugins(f.home, "~/.zcode"));
  } finally { process.env = originalEnv; }
});
test("invalid HOME or env arguments fail closed", () => {
  for (const home of [undefined, null, [], {}, "", " \t"]) assert.throws(() => resolvePluginStorage(home, {}), /home 必须是非空字符串/);
  for (const env of [null, [], "environment"]) assert.throws(() => resolvePluginStorage(os.tmpdir(), env), /env 必须是对象/);
});
