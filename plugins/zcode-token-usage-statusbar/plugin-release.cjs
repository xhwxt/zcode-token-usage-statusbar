"use strict";
const https = require("https");
const fs = require("fs");
const path = require("path");
const os = require("os");
const crypto = require("crypto");
const { execFile } = require("child_process");

const PLUGIN_ID = "zcode-token-usage-statusbar@zusage-marketplace";
const STABLE = /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/;
const MAX_ASSET = 16 * 1024 * 1024;
const MAX_JSON = 256 * 1024;

function githubRepository(value) {
  if (typeof value !== "string") return null;
  const match = /^(?:https:\/\/github\.com\/|ssh:\/\/git@github\.com\/|git@github\.com:)?([A-Za-z0-9][A-Za-z0-9-]*\/[A-Za-z0-9][A-Za-z0-9._-]*?)(?:\.git)?\/?$/.exec(value);
  return match ? match[1] : null;
}
function readChannel(home, root = require("./plugin-storage.cjs").resolvePluginStorage(home)) {
  const file = path.join(root, "known_marketplaces.json");
  const data = JSON.parse(fs.readFileSync(file, "utf8"));
  const entries = data && data.marketplaces;
  if (!Array.isArray(entries)) throw new Error("插件市场来源记录无效");
  const matches = entries.filter((m) => m && (m.id === "zusage-marketplace" || m.name === "zusage-marketplace"));
  const market = matches.length === 1 ? matches[0] : null;
  if (!market || !market.source || typeof market.source !== "object") throw new Error("未找到 zusage-marketplace 的注册来源");
  const source = market.source;
  if (source.source === "directory") return { kind: "local", marketplace: market };
  const repository = source.source === "github" ? githubRepository(source.repo || source.repository)
    : source.source === "git" ? githubRepository(source.url) : null;
  if (!repository) throw new Error("远端自动更新仅支持已注册的 GitHub 仓库来源，不能回退开发分支");
  return { kind: "release", repository, marketplace: market };
}
async function fetchBytes(url, limit, redirects = 0) {
  const parsed = new URL(url);
  const allowed = parsed.protocol === "https:" && !parsed.username && !parsed.password && !parsed.port &&
    ["api.github.com", "github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"].includes(parsed.hostname);
  if (!allowed) return Promise.reject(new Error("拒绝非 GitHub HTTPS 更新地址"));
  let timer;
  return new Promise((resolve, reject) => {
    const request = https.get(parsed, { headers: { "User-Agent": "zcode-token-usage-statusbar-release-updater",
      "Accept": parsed.hostname === "api.github.com" ? "application/vnd.github+json" : "application/octet-stream",
      "X-GitHub-Api-Version": "2022-11-28" } }, (response) => {
      if ([301, 302, 303, 307, 308].includes(response.statusCode)) {
        response.resume();
        if (!response.headers.location || redirects >= 3) { reject(new Error("GitHub 下载重定向无效")); return; }
        try { fetchBytes(new URL(response.headers.location, parsed).href, limit, redirects + 1).then(resolve, reject); }
        catch (error) { reject(error); }
        return;
      }
      if (response.statusCode !== 200) {
        response.resume(); reject(new Error(`GitHub Release 请求失败 (${response.statusCode})`)); return;
      }
      const declared = Number(response.headers["content-length"] || 0);
      if (declared > limit) { response.destroy(); reject(new Error("GitHub 发布资源超过大小上限")); return; }
      const chunks = []; let length = 0;
      response.on("data", (chunk) => {
        length += chunk.length;
        if (length > limit) { response.destroy(); reject(new Error("GitHub 发布资源超过大小上限")); }
        else chunks.push(chunk);
      });
      response.on("error", reject);
      response.on("aborted", () => reject(new Error("GitHub 发布资源下载中断")));
      response.on("end", () => {
        if (!response.complete || (declared && declared !== length)) reject(new Error("GitHub 发布资源下载不完整"));
        else resolve(Buffer.concat(chunks));
      });
    });
    timer = setTimeout(() => { const error = new Error("GitHub Release 请求超过总时限"); request.destroy(error); reject(error); }, 60000);
    request.setTimeout(30000, () => request.destroy(new Error("GitHub Release 请求超时")));
    request.on("error", reject);
  }).finally(() => clearTimeout(timer));
}
function releaseAssetUrl(repository, tag, name) {
  return `https://github.com/${repository}/releases/download/${encodeURIComponent(tag)}/${encodeURIComponent(name)}`;
}
function validateRelease(release, repository) {
  if (!release || release.draft !== false || release.prerelease !== false || typeof release.tag_name !== "string") return null;
  const version = release.tag_name.startsWith("v") ? release.tag_name.slice(1) : "";
  if (!STABLE.test(version)) return null;
  const names = [`zcode-token-usage-statusbar-v${version}.zip`, "release-manifest.json"];
  const assets = Array.isArray(release.assets) ? release.assets : [];
  const picked = names.map((name) => assets.filter((a) => a && a.name === name));
  if (picked.some((matches) => matches.length !== 1)) return null;
  const [archive, manifest] = picked.map((matches) => matches[0]);
  for (const asset of [archive, manifest]) {
    const limit = asset === archive ? MAX_ASSET : MAX_JSON;
    if (asset.state !== "uploaded" || !Number.isSafeInteger(asset.size) || asset.size <= 0 || asset.size > limit ||
        asset.browser_download_url !== releaseAssetUrl(repository, release.tag_name, asset.name)) throw new Error("GitHub Release 资产来源或大小无效");
  }
  return { version, tag: release.tag_name, repository, archive, manifest,
    url: `https://github.com/${repository}/releases/tag/${encodeURIComponent(release.tag_name)}` };
}
async function checkRelease(repository, compareVersions, fetch = fetchBytes) {
  if (!githubRepository(repository) || repository !== githubRepository(repository)) throw new Error("GitHub 仓库标识无效");
  let best = null;
  for (let page = 1; page <= 10; page++) {
    const body = await fetch(`https://api.github.com/repos/${repository}/releases?per_page=100&page=${page}`, 4 * 1024 * 1024);
    const rows = JSON.parse(body.toString("utf8"));
    if (!Array.isArray(rows)) throw new Error("GitHub Release 列表格式无效");
    for (const row of rows) {
      const candidate = validateRelease(row, repository);
      if (candidate && (!best || compareVersions(candidate.version, best.version) > 0)) best = candidate;
    }
    if (rows.length < 100) {
      if (!best) throw new Error("此 GitHub 仓库尚无带完整发布包的正式 Release，不能安装开发分支");
      return best;
    }
  }
  throw new Error("GitHub Release 列表超过安全分页上限，无法确定最新正式版本");
}
function validateManifest(manifest, release) {
  const asset = manifest && manifest.asset;
  if (!manifest || manifest.schemaVersion !== 1 || manifest.repository !== release.repository ||
      manifest.version !== release.version || manifest.tag !== release.tag || manifest.pluginId !== PLUGIN_ID || !asset ||
      asset.name !== release.archive.name || asset.size !== release.archive.size ||
      !Number.isSafeInteger(asset.size) || asset.size <= 0 || asset.size > MAX_ASSET ||
      typeof asset.sha256 !== "string" || !/^[a-f0-9]{64}$/.test(asset.sha256)) throw new Error("发布 manifest 与 GitHub 版本、来源或资源不一致");
  return manifest;
}
function unpack(command, args) {
  return new Promise((resolve, reject) => execFile(command, args,
    { windowsHide: true, timeout: 30000, maxBuffer: 1024 * 1024 }, (error, stdout, stderr) => {
      if (error) { reject(new Error(String(stderr || error.message).trim().slice(0, 1000))); return; }
      try { resolve(JSON.parse(stdout)); } catch (e) { reject(new Error("发布包校验器未返回有效 JSON")); }
    }));
}
async function downloadRelease(release, python, fetch = fetchBytes, runUnpack = unpack) {
  const manifestBody = await fetch(release.manifest.browser_download_url, MAX_JSON);
  const manifest = validateManifest(JSON.parse(manifestBody.toString("utf8")), release);
  const bytes = await fetch(release.archive.browser_download_url, MAX_ASSET);
  if (bytes.length !== manifest.asset.size || crypto.createHash("sha256").update(bytes).digest("hex") !== manifest.asset.sha256) throw new Error("发布包下载大小或 SHA256 校验失败");
  const directory = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "zusage-release-")));
  const cleanup = () => fs.rmSync(directory, { recursive: true, force: true });
  try {
    const archive = path.join(directory, release.archive.name), info = path.join(directory, "release-manifest.json");
    const output = path.join(directory, "marketplace");
    fs.writeFileSync(archive, bytes, { flag: "wx" }); fs.writeFileSync(info, manifestBody, { flag: "wx" });
    const result = await runUnpack(python, [path.join(__dirname, "release_unpack.py"), "--archive", archive,
      "--manifest", info, "--repository", release.repository, "--version", release.version, "--output", output]);
    if (!result || result.ok !== true || result.version !== release.version || result.marketplaceRoot !== output ||
        result.pluginRoot !== path.join(output, "plugins", "zcode-token-usage-statusbar")) throw new Error("发布包校验或解压结果无效");
    return { directory, marketplaceRoot: output, pluginRoot: result.pluginRoot, release, manifest, cleanup };
  } catch (error) { cleanup(); throw error; }
}
function validateCatalog(catalog, release, manifest) {
  const entries = catalog && catalog.plugins;
  const matches = Array.isArray(entries) ? entries.filter((p) => p && p.name === "zcode-token-usage-statusbar") : [];
  const entry = matches.length === 1 ? matches[0] : null;
  const source = entry && entry.source;
  if (!catalog || catalog.name !== "zusage-marketplace" || !entry || entry.version !== release.version ||
      !source || typeof source !== "object" || source.source !== "url" || source.type !== "zip" ||
      source.url !== release.archive.browser_download_url || source.sha256 !== manifest.asset.sha256 ||
      source.path !== "plugins/zcode-token-usage-statusbar" || (source.stripRoot !== undefined && source.stripRoot !== false) ||
      source.headers !== undefined) throw new Error("市场尚未发布与正式 Release 一致的校验下载源；请维护者发布 Release 渠道清单，不能回退开发分支");
  return entry;
}
module.exports = { githubRepository, readChannel, checkRelease, downloadRelease, validateRelease, validateManifest, validateCatalog,
  fetchBytes, MAX_ASSET, MAX_JSON, PLUGIN_ID };
