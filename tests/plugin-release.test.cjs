"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const path = require("path");
const os = require("os");
const crypto = require("crypto");
const https = require("https");
const { EventEmitter } = require("events");
const { githubRepository, readChannel, checkRelease, downloadRelease, validateRelease, validateManifest, validateCatalog,
  fetchBytes, MAX_ASSET, MAX_JSON, PLUGIN_ID } = require("../plugin-release.cjs");
const { compareVersions } = require("../plugin-updater.cjs");

function release(version, repository = "ByteRax/zcode-token-usage-statusbar") {
  const base = `https://github.com/${repository}/releases/download/v${version}/`;
  return { tag_name: "v" + version, draft: false, prerelease: false,
    assets: [{ name: `zcode-token-usage-statusbar-v${version}.zip`, state: "uploaded", size: 3,
      browser_download_url: base + `zcode-token-usage-statusbar-v${version}.zip` },
    { name: "release-manifest.json", state: "uploaded", size: 512, browser_download_url: base + "release-manifest.json" }] };
}
function fixture(t, source) {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "zusage-channel-"));
  t.after(() => fs.rmSync(home, { recursive: true, force: true }));
  const dir = path.join(home, ".zcode", "cli", "plugins"); fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, "known_marketplaces.json"), JSON.stringify({ marketplaces: [{ id: "zusage-marketplace", source }] }));
  return home;
}
function channel(home) { return readChannel(home, path.join(home, ".zcode", "cli", "plugins")); }
function packageFixture() {
  const selected = validateRelease(release("1.4.0"), "ByteRax/zcode-token-usage-statusbar"), bytes = Buffer.from("zip");
  const manifest = { schemaVersion: 1, repository: selected.repository, version: selected.version, tag: selected.tag,
    pluginId: PLUGIN_ID, asset: { name: selected.archive.name, size: bytes.length,
      sha256: crypto.createHash("sha256").update(bytes).digest("hex") } };
  const fetch = async (url) => url === selected.manifest.browser_download_url ? Buffer.from(JSON.stringify(manifest)) : bytes;
  return { selected, bytes, manifest, fetch };
}
function mockHttps(t, routes) {
  const calls = [];
  t.mock.method(https, "get", (url, options, callback) => {
    const route = routes[calls.length]; calls.push(url.href);
    assert.ok(route, `unexpected request: ${url.href}`);
    assert.equal(options.headers["User-Agent"], "zcode-token-usage-statusbar-release-updater");
    const request = new EventEmitter(); let socketTimeout;
    request.setTimeout = (ms, handler) => { assert.equal(ms, 30000); socketTimeout = handler; return request; };
    request.destroy = (error) => { queueMicrotask(() => request.emit("error", error)); return request; };
    queueMicrotask(() => {
      if (route.requestError) { request.emit("error", route.requestError); return; }
      if (route.socketTimeout) { socketTimeout(); return; }
      const response = new EventEmitter();
      response.statusCode = route.status || 200; response.headers = route.headers || {}; response.complete = route.complete !== false;
      response.resume = () => {}; response.destroy = () => { response.destroyed = true; };
      callback(response);
      if (response.statusCode !== 200 || response.destroyed) return;
      for (const chunk of route.chunks || [Buffer.from("zip")]) {
        response.emit("data", chunk); if (response.destroyed) return;
      }
      if (route.error) response.emit("error", route.error);
      else if (route.aborted) response.emit("aborted");
      else response.emit("end");
    });
    return request;
  });
  return calls;
}
test("registered directory stays local and GitHub/git repository selects release without rebinding", (t) => {
  assert.equal(channel(fixture(t, { source: "directory", path: "/dev" })).kind, "local");
  assert.equal(channel(fixture(t, { source: "github", repo: "ByteRax/zcode-token-usage-statusbar", ref: "plugin-releases" })).repository, "ByteRax/zcode-token-usage-statusbar");
  assert.equal(channel(fixture(t, { source: "git", url: "https://github.com/ByteRax/zcode-token-usage-statusbar.git" })).kind, "release");
  assert.throws(() => channel(fixture(t, { source: "git", url: "https://gitlab.com/owner/repo.git" })), /GitHub/);
});
test("repository validation does not accept credentials, query, non-GitHub URLs or branch suffix", () => {
  assert.equal(githubRepository("git@github.com:ByteRax/project.git"), "ByteRax/project");
  for (const value of ["https://evil.com/owner/repo", "https://token@github.com/owner/repo", "owner/repo#main", "owner/repo?x=1", "../repo"]) assert.equal(githubRepository(value), null);
});
test("formal release selection ignores screenshots, drafts and prereleases and compares numeric versions", async () => {
  const rows = [release("1.9.0"), { ...release("2.0.0"), draft: true }, { ...release("3.0.0"), prerelease: true },
    { ...release("4.0.0"), tag_name: "screenshots-v65-hover-bubble" }, release("1.10.0")];
  const selected = await checkRelease("ByteRax/zcode-token-usage-statusbar", compareVersions, async () => Buffer.from(JSON.stringify(rows)));
  assert.equal(selected.version, "1.10.0");
});
test("no proper assets or corrupted release metadata never falls back to a development branch", async () => {
  const repo = "ByteRax/zcode-token-usage-statusbar";
  await assert.rejects(checkRelease(repo, compareVersions, async () => Buffer.from("[]")), /正式 Release/);
  const bad = release("1.0.0"); bad.assets[0].browser_download_url = "https://evil.com/payload.zip";
  await assert.rejects(checkRelease(repo, compareVersions, async () => Buffer.from(JSON.stringify([bad]))), /来源/);
  await assert.rejects(fetchBytes("http://github.com/a/b", 100), /HTTPS/);
});
test("download verifies source manifest, asset hash and unpack result and cleans its temp only", async () => {
  const repo = "ByteRax/zcode-token-usage-statusbar";
  const selected = await checkRelease(repo, compareVersions, async () => Buffer.from(JSON.stringify([release("1.4.0")])));
  const bytes = Buffer.from("zip");
  const manifest = { schemaVersion: 1, repository: repo, version: "1.4.0", tag: "v1.4.0",
    pluginId: "zcode-token-usage-statusbar@zusage-marketplace", asset: { name: selected.archive.name, size: bytes.length,
      sha256: crypto.createHash("sha256").update(bytes).digest("hex") } };
  const fetch = async (url) => url.endsWith("release-manifest.json") ? Buffer.from(JSON.stringify(manifest)) : bytes;
  let output;
  const downloaded = await downloadRelease(selected, "python3", fetch, async (command, args) => {
    assert.equal(command, "python3"); output = args[args.indexOf("--output") + 1];
    return { ok: true, marketplaceRoot: output, pluginRoot: path.join(output, "plugins", "zcode-token-usage-statusbar"), version: "1.4.0" };
  });
  assert.ok(fs.existsSync(downloaded.directory)); downloaded.cleanup(); assert.equal(fs.existsSync(downloaded.directory), false);
  assert.throws(() => validateManifest({ ...manifest, repository: "attacker/repo" }, selected), /不一致/);
  await assert.rejects(downloadRelease(selected, "python3", async (url) => url.endsWith("release-manifest.json") ? Buffer.from(JSON.stringify(manifest)) : Buffer.from("bad")), /SHA256/);
});
test("catalog rejects duplicate targets and every release source mismatch", () => {
  const { selected, manifest } = packageFixture();
  const entry = { name: "zcode-token-usage-statusbar", version: selected.version, source: { source: "url", type: "zip",
    url: selected.archive.browser_download_url, sha256: manifest.asset.sha256, path: "plugins/zcode-token-usage-statusbar" } };
  const catalog = { name: "zusage-marketplace", plugins: [entry] };
  assert.equal(validateCatalog(catalog, selected, manifest), entry);
  validateCatalog({ ...catalog, plugins: [{ ...entry, source: { ...entry.source, stripRoot: false } }] }, selected, manifest);
  const invalid = [null, { ...catalog, name: "other-market" }, { ...catalog, plugins: [] }, { ...catalog, plugins: [entry, entry] },
    { ...catalog, plugins: [{ ...entry, version: "1.5.0" }] }, ...[
      { source: "git" }, { type: "git" }, { url: "https://github.com/attacker/repo/releases/download/v1.4.0/asset.zip" },
      { sha256: "0".repeat(64) }, { path: "../plugins/zcode-token-usage-statusbar" }, { path: undefined },
      { stripRoot: true }, { stripRoot: null }, { headers: {} }, { headers: { Authorization: "fixture" } },
    ].map((source) => ({ ...catalog, plugins: [{ ...entry, source: { ...entry.source, ...source } }] }))];
  for (const value of invalid) assert.throws(() => validateCatalog(value, selected, manifest), /不能回退开发分支/);
});
test("channel rejects untrusted repositories and duplicate known marketplace identities without writing", (t) => {
  for (const source of [{ source: "github", repo: "owner/repo#main" }, { source: "git", url: "https://token@github.com/owner/repo.git" },
    { source: "git", url: "https://github.com/owner/repo.git?ref=main" }, { source: "url", url: "https://github.com/owner/repo" }]) {
    assert.throws(() => channel(fixture(t, source)), /不能回退开发分支/);
  }
  const home = fixture(t, { source: "github", repo: "ByteRax/zcode-token-usage-statusbar" });
  const file = path.join(home, ".zcode", "cli", "plugins", "known_marketplaces.json");
  const record = JSON.parse(fs.readFileSync(file)).marketplaces[0];
  for (const duplicate of [record, { name: "zusage-marketplace", source: record.source }]) {
    const body = JSON.stringify({ marketplaces: [record, duplicate] }); fs.writeFileSync(file, body);
    assert.throws(() => channel(home), /注册来源/); assert.equal(fs.readFileSync(file, "utf8"), body);
  }
});
test("release selection paginates and refuses ambiguous pagination, absent formal releases and fetch failures", async () => {
  const repo = "ByteRax/zcode-token-usage-statusbar", calls = [];
  const first = Array.from({ length: 100 }, () => ({ tag_name: "fixture-screenshot", draft: false, prerelease: false }));
  first[0] = release("1.9.0");
  const selected = await checkRelease(repo, compareVersions, async (url, limit) => {
    calls.push(url); assert.equal(limit, 4 * 1024 * 1024);
    return Buffer.from(JSON.stringify(calls.length === 1 ? first : [release("1.10.0")]));
  });
  assert.equal(selected.version, "1.10.0");
  assert.deepEqual(calls, [1, 2].map((page) => `https://api.github.com/repos/${repo}/releases?per_page=100&page=${page}`));
  let pages = 0;
  await assert.rejects(checkRelease(repo, compareVersions, async () => { pages++; return Buffer.from(JSON.stringify(first)); }), /分页上限/);
  assert.equal(pages, 10);
  for (const rows of [[], [{ ...release("1.0.0"), draft: true }], [{ ...release("1.0.0"), prerelease: true }],
    [{ ...release("1.0.0"), assets: [] }], [{ ...release("1.0.0"), assets: [...release("1.0.0").assets, release("1.0.0").assets[0]] }]]) {
    await assert.rejects(checkRelease(repo, compareVersions, async () => Buffer.from(JSON.stringify(rows))), /正式 Release/);
  }
  for (const body of ["{bad", "{}"]) await assert.rejects(checkRelease(repo, compareVersions, async () => Buffer.from(body)));
  for (const status of [404, 429]) {
    const error = new Error(`fixture HTTP ${status}`);
    await assert.rejects(checkRelease(repo, compareVersions, async () => { throw error; }), (actual) => actual === error);
  }
});
test("release assets and manifest reject size, hash and repository identity mismatches", () => {
  const { selected, manifest } = packageFixture();
  for (const index of [0, 1]) {
    for (const patch of [{ state: "new" }, { size: 0 }, { size: 1.5 }, { size: index ? MAX_JSON + 1 : MAX_ASSET + 1 },
      { browser_download_url: "https://github.com/attacker/repo/releases/download/v1.4.0/asset.zip" }]) {
      const row = release("1.4.0"); Object.assign(row.assets[index], patch);
      assert.throws(() => validateRelease(row, selected.repository), /来源或大小/);
    }
  }
  for (const patch of [{ schemaVersion: 2 }, { repository: "attacker/repo" }, { pluginId: "other@zusage-marketplace" },
    { version: "1.5.0" }, { tag: "v1.5.0" }, ...[{ name: "other.zip" }, { size: 4 }, { sha256: "invalid" }]
      .map((asset) => ({ asset: { ...manifest.asset, ...asset } }))]) {
    assert.throws(() => validateManifest({ ...manifest, ...patch }, selected), /不一致/);
  }
});
test("download failures never unpack invalid data and clean only their temporary directory", async (t) => {
  const f = packageFixture(), parent = fs.mkdtempSync(path.join(os.tmpdir(), "zusage-release-test-"));
  t.after(() => fs.rmSync(parent, { recursive: true, force: true }));
  const sentinel = path.join(parent, "keep"); fs.writeFileSync(sentinel, "fixture");
  t.mock.method(os, "tmpdir", () => parent);
  let unpacked = 0;
  const forbidden = async () => { unpacked++; throw new Error("must not unpack"); };
  for (const fetch of [async () => { throw new Error("fixture fetch failed"); },
    async () => Buffer.from("{bad"), async () => Buffer.from(JSON.stringify({ ...f.manifest, repository: "attacker/repo" })),
    async (url) => url === f.selected.manifest.browser_download_url ? Buffer.from(JSON.stringify(f.manifest)) : Buffer.from("z"),
    async (url) => url === f.selected.manifest.browser_download_url ? Buffer.from(JSON.stringify(f.manifest)) : Buffer.from("bad"),
    async (url) => { if (url === f.selected.archive.browser_download_url) throw new Error("fixture archive failed"); return Buffer.from(JSON.stringify(f.manifest)); }]) {
    await assert.rejects(downloadRelease(f.selected, "python3", fetch, forbidden));
    assert.deepEqual(fs.readdirSync(parent), ["keep"]);
  }
  assert.equal(unpacked, 0);
  for (const invalid of [null, { ok: false }, { version: "1.5.0" }, { marketplaceRoot: parent }, { pluginRoot: parent }, "throw"]) {
    let directory;
    await assert.rejects(downloadRelease(f.selected, "python3", f.fetch, async (command, args) => {
      assert.equal(command, "python3"); assert.equal(args[args.indexOf("--repository") + 1], f.selected.repository);
      const archive = args[args.indexOf("--archive") + 1], output = args[args.indexOf("--output") + 1];
      directory = path.dirname(archive); assert.ok(fs.existsSync(archive));
      if (invalid === "throw") throw new Error("fixture unpack failed");
      return invalid === null ? null : { ok: true, version: f.selected.version, marketplaceRoot: output,
        pluginRoot: path.join(output, "plugins", "zcode-token-usage-statusbar"), ...invalid };
    }), /无效|fixture unpack failed/);
    assert.equal(fs.existsSync(directory), false); assert.deepEqual(fs.readdirSync(parent), ["keep"]);
  }
  assert.equal(fs.readFileSync(sentinel, "utf8"), "fixture");
});
test("fetch mock accepts valid redirect and rejects invalid redirect destinations or loops", async (t) => {
  const routes = [], calls = mockHttps(t, routes), url = "https://github.com/owner/repo/asset.zip";
  routes.push({ status: 302, headers: { location: "https://release-assets.githubusercontent.com/fixture.zip" } }, {});
  assert.equal((await fetchBytes(url, 10)).toString(), "zip"); assert.equal(calls.length, 2);
  for (const location of [undefined, "http://github.com/owner/repo", "https://evil.invalid/asset", "https://user@github.com/a/b", "http://["]) {
    routes.push({ status: 302, headers: location === undefined ? {} : { location } });
    const before = calls.length; await assert.rejects(fetchBytes(url, 10)); assert.equal(calls.length, before + 1);
  }
  routes.push(...Array.from({ length: 4 }, () => ({ status: 302, headers: { location: "/loop" } })));
  await assert.rejects(fetchBytes(url, 10), /重定向无效/);
});
test("fetch mock rejects HTTP errors, aborted or incomplete streams, oversized bodies and socket errors", async (t) => {
  const routes = [], calls = mockHttps(t, routes), url = "https://api.github.com/repos/owner/repo/releases";
  for (const status of [403, 404, 429]) {
    routes.push({ status }); await assert.rejects(fetchBytes(url, 10), new RegExp(String(status)));
  }
  for (const [route, pattern] of [[{ aborted: true }, /中断/], [{ complete: false }, /不完整/],
    [{ headers: { "content-length": "4" } }, /不完整/], [{ headers: { "content-length": "11" } }, /大小上限/],
    [{ chunks: [Buffer.from("123456"), Buffer.from("123456")] }, /大小上限/],
    [{ requestError: new Error("fixture socket error") }, /fixture socket error/], [{ socketTimeout: true }, /请求超时/]]) {
    routes.push(route); await assert.rejects(fetchBytes(url, 10), pattern);
  }
  routes.push({ headers: { "content-length": "3" } }); assert.equal((await fetchBytes(url, 10)).toString(), "zip");
  const before = calls.length;
  for (const denied of ["http://github.com/a/b", "https://user@github.com/a/b", "https://github.com:444/a/b", "https://evil.invalid/a/b"]) {
    await assert.rejects(fetchBytes(denied, 10), /HTTPS/);
  }
  assert.equal(calls.length, before);
});
