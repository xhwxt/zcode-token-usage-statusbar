"use strict";
const fs = require("fs");
const path = require("path");

function storageDir(value, source) {
  if (typeof value !== "string" || !value.trim()) throw new Error(`${source} 必须是非空字符串`);
  return value;
}
function readStorageDir(file) {
  let text;
  try { text = fs.readFileSync(file, "utf8"); }
  catch (error) {
    if (error.code === "ENOENT") return undefined;
    throw new Error(`无法读取插件存储配置 ${file}: ${error.message}`);
  }
  let config;
  try { config = JSON.parse(text); }
  catch (error) { throw new Error(`插件存储配置 JSON 损坏 ${file}: ${error.message}`); }
  if (!config || typeof config !== "object" || Array.isArray(config)) throw new Error(`插件存储配置必须是对象: ${file}`);
  if (!Object.prototype.hasOwnProperty.call(config, "storage")) return undefined;
  if (!config.storage || typeof config.storage !== "object" || Array.isArray(config.storage)) throw new Error(`storage 必须是对象: ${file}`);
  if (!Object.prototype.hasOwnProperty.call(config.storage, "dir")) return undefined;
  return storageDir(config.storage.dir, `${file} 的 storage.dir`);
}
function projectDirectories(home) {
  const directories = [];
  let directory = home;
  for (;;) {
    directories.push(directory);
    let git;
    try { git = fs.statSync(path.join(directory, ".git")); }
    catch (error) {
      if (error.code !== "ENOENT") throw new Error(`无法识别插件存储配置的 Git 边界 ${directory}: ${error.message}`);
    }
    if (git && (git.isDirectory() || git.isFile())) return directories.reverse();
    const parent = path.dirname(directory);
    if (parent === directory) return [home];
    directory = parent;
  }
}
function resolvePluginStorage(home, env = process.env) {
  storageDir(home, "home");
  if (!env || typeof env !== "object" || Array.isArray(env)) throw new Error("env 必须是对象");
  home = path.resolve(home);
  let directory = "~/.zcode";
  const files = [path.join(home, ".zcode", "cli", "config.json")];
  for (const project of projectDirectories(home)) {
    files.push(path.join(project, "zcode.json"), path.join(project, ".zcode", "config.json"));
  }
  // 每次读取全部依赖；即使被高优先级设置覆盖，损坏配置也不得回退。
  for (const file of files) {
    const value = readStorageDir(file);
    if (value !== undefined) directory = value;
  }
  if (env.ZCODE_STORAGE_DIR !== undefined) directory = storageDir(env.ZCODE_STORAGE_DIR, "ZCODE_STORAGE_DIR");
  const root = directory.startsWith("~/") ? path.join(home, directory.slice(2)) : path.resolve(home, directory);
  return path.join(root, ...(path.basename(root) === "cli" ? [] : ["cli"]), "plugins");
}

module.exports = { resolvePluginStorage };
