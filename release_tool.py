#!/usr/bin/env python3
"""Build/verify local release assets; publishing requires two explicit approvals."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tempfile
import zipfile

PLUGIN = "zcode-token-usage-statusbar"
PREFIX = f"plugins/{PLUGIN}/"
PLUGIN_ID = f"{PLUGIN}@zusage-marketplace"
SEMVER = r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
MAX_FILE = 8 * 1024 * 1024
MAX_ASSET = 16 * 1024 * 1024
MAX_MANIFEST = 256 * 1024
MAX_TOTAL = 64 * 1024 * 1024
SYNC_FILES = ("usage_mcp.py", "zusage.py", "patch_install.py", "install_monitor.py",
              "inject-main.cjs", "plugin-updater.cjs", "runtime_lock.py", "overlay.js",
              "plugin-release.cjs", "plugin-storage.cjs", "release_unpack.py")
# Enumerated source paths, never a recursive glob that could collect local usage data.
SOURCES = (*SYNC_FILES, "overlay_install.py", "token_rate.py", "plugin_auto_update.py",
           "restart_notice.py", ".mcp.json", "README.md", ".zcode-plugin/plugin.json",
           ".claude-plugin/plugin.json", "hooks/hooks.json", "hooks/session-start.py",
           "hooks/prompt-submit.py", "commands/usage.md", "commands/install-overlay.md",
           "commands/uninstall-overlay.md", "skills/zcode-token-usage/SKILL.md")
NAMES = tuple(sorted(("LICENSE", "marketplace.json", *(PREFIX + p for p in SOURCES))))
RELEVANT = ("LICENSE", "marketplace.json", "plugins", "release_tool.py", "sync_plugin.py", *SYNC_FILES)


class ReleaseError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise ReleaseError(message)


def repository(value):
    require(isinstance(value, str) and re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9][A-Za-z0-9._-]*", value),
        "repository must be explicit owner/repo (GitHub only)")
    return value


def read_file(path, limit=MAX_FILE):
    require(not path.is_symlink() and path.is_file(), f"not a regular source file: {path}")
    require(path.stat().st_size <= limit, f"oversize file: {path}")
    return path.read_bytes()


def identities(data, expected=None):
    market = json.loads(data["marketplace.json"])
    manifest = json.loads(data[PREFIX + ".zcode-plugin/plugin.json"])
    compatibility = json.loads(data[PREFIX + ".claude-plugin/plugin.json"])
    require(all(isinstance(value, dict) for value in (market, manifest, compatibility)),
            "project manifests must be JSON objects")
    version = manifest.get("version")
    require(isinstance(version, str) and re.fullmatch(SEMVER, version), "stable SEMVER required")
    require(manifest == compatibility and manifest.get("name") == PLUGIN,
            "plugin manifests/name disagree")
    require(market.get("name") == "zusage-marketplace", "marketplace name disagrees")
    entries = market.get("plugins", [])
    require(isinstance(entries, list) and len(entries) == 1, "unexpected marketplace plugins")
    entry = entries[0]
    require(isinstance(entry, dict), "marketplace entry must be an object")
    require(entry.get("name") == PLUGIN and entry.get("version") == version and
            entry.get("source") == "./plugins/" + PLUGIN, "marketplace version/name/source disagrees")
    require(manifest.get("commands") == "commands" and manifest.get("skills") == "skills",
            "plugin component paths disagree")
    require(expected is None or expected == version, "requested version disagrees")
    return version


def source_data(root, expected=None):
    plugin = root / "plugins" / PLUGIN
    require(plugin.is_dir() and not plugin.is_symlink() and not (root / "plugins").is_symlink(),
            "missing or symlink plugin source directory")
    for directory, dirs, files in os.walk(plugin, followlinks=False):
        for name in dirs + files:
            require(not (Path(directory) / name).is_symlink(), "symlink in plugin tree")
    data = {name: read_file(root / name) for name in NAMES}
    require(sum(map(len, data.values())) <= MAX_TOTAL, "oversize source tree")
    identities(data, expected)
    for name in SYNC_FILES:
        require(read_file(root / name) == data[PREFIX + name], f"runtime copy drift: {name}")
    return data


def zip_name(version):
    return f"{PLUGIN}-v{version}.zip"


def build(root, repo, out, version=None):
    repo = repository(repo)
    data = source_data(root, version)
    version = identities(data, version)
    require(not out.exists() and not out.is_symlink(), "output directory already exists")
    out.mkdir(parents=True, exist_ok=False)
    asset = out / zip_name(version)
    with zipfile.ZipFile(asset, "x", compression=zipfile.ZIP_STORED) as archive:
        for name in NAMES:
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(info, data[name])
    payload = read_file(asset, MAX_ASSET)
    manifest = {"schemaVersion": 1, "repository": repo, "version": version, "tag": "v" + version,
                "pluginId": PLUGIN_ID, "asset": {"name": asset.name, "size": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest()}}
    path = out / "release-manifest.json"
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    verify(asset, path, repo)
    catalog = json.loads(data["marketplace.json"])
    catalog["plugins"][0]["source"] = {
        "source": "url", "type": "zip", "url":
        f"https://github.com/{repo}/releases/download/v{version}/{asset.name}",
        "sha256": manifest["asset"]["sha256"], "path": "plugins/" + PLUGIN, "stripRoot": False}
    catalog_path = out / "marketplace-release.json"
    catalog_path.write_text(json.dumps(catalog, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"zip": str(asset.resolve()), "manifest": str(path.resolve()),
            "catalog": str(catalog_path.resolve()), **manifest}


def verify(asset, manifest_path, repo=None):
    require(asset.name != manifest_path.name and manifest_path.name == "release-manifest.json",
            "expected separate release-manifest.json")
    manifest = json.loads(read_file(manifest_path, MAX_MANIFEST))
    require(isinstance(manifest, dict), "release manifest must be a JSON object")
    repository(manifest.get("repository"))
    require(repo is None or repository(repo) == manifest["repository"], "repository disagrees")
    version = manifest.get("version")
    require(isinstance(version, str) and re.fullmatch(SEMVER, version), "stable SEMVER required")
    require(type(manifest.get("schemaVersion")) is int and manifest["schemaVersion"] == 1 and
            manifest.get("tag") == "v" + version and manifest.get("pluginId") == PLUGIN_ID,
            "release manifest identity disagrees")
    record = manifest.get("asset", {})
    require(isinstance(record, dict), "asset record must be a JSON object")
    payload = read_file(asset, MAX_ASSET)
    require(asset.name == record.get("name") == zip_name(version) and
            type(record.get("size")) is int and record["size"] == len(payload) and
            record.get("sha256") == hashlib.sha256(payload).hexdigest(), "asset name/size/hash disagrees")
    # Check bounded compression/resources before our ZIP reads; never import ZIP code.
    helper_path = Path(__file__).resolve().with_name("release_unpack.py")
    read_file(helper_path)
    spec = importlib.util.spec_from_file_location("release_tool_unpack", helper_path)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    with tempfile.TemporaryDirectory(prefix="zusage-release-verify-") as temporary:
        try:
            helper.unpack(asset.absolute(), manifest_path.absolute(), manifest["repository"], version,
                          Path(temporary) / "marketplace")
        except (ValueError, OSError, TypeError, RuntimeError, EOFError, RecursionError,
                zipfile.BadZipFile, zipfile.LargeZipFile, helper.zlib.error) as error:
            raise ReleaseError(f"release unpack validation failed: {error}") from error
    with zipfile.ZipFile(asset) as archive:
        members = archive.infolist()
        require(len(members) == len(NAMES), "unexpected ZIP entries or duplicate names")
        data = {}
        total = 0
        for member in members:
            name = member.filename
            path = PurePosixPath(name)
            mode = member.external_attr >> 16
            require(name in NAMES and not path.is_absolute() and ".." not in path.parts and
                    "\\" not in name and ":" not in name and name not in data,
                    f"unsafe or non-source ZIP entry: {name}")
            require(not member.is_dir() and stat.S_IFMT(mode) in (0, stat.S_IFREG) and
                    not member.flag_bits & 1 and member.compress_type in
                    (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED), "non-regular/encrypted/unsupported ZIP entry")
            total += member.file_size
            require(member.file_size <= MAX_FILE and total <= MAX_TOTAL, "oversize ZIP contents")
            data[name] = archive.read(member)
        require(set(data) == set(NAMES), "missing ZIP source")
        identities(data, version)
    return manifest


def run(root, *args):
    result = subprocess.run(args, cwd=root, capture_output=True, check=False)
    require(result.returncode == 0, f"command failed: {' '.join(args)}\n" +
            result.stderr.decode("utf-8", errors="replace"))
    return result.stdout


def gh_json(root, endpoint, *flags):
    return json.loads(run(root, "gh", "api", endpoint, "--hostname", "github.com",
                          "--method", "GET", *flags))


def preflight(root, repo, manifest, asset):
    data = source_data(root, manifest["version"])
    with zipfile.ZipFile(asset) as archive:
        require(all(archive.read(name) == content for name, content in data.items()),
                "ZIP is not the current repository source")
    top = run(root, "git", "rev-parse", "--show-toplevel").decode().strip()
    require(Path(top).resolve() == root.resolve(), "--root must be the git repository root")
    head = run(root, "git", "rev-parse", "--verify", "HEAD^{commit}").decode().strip()
    require(not run(root, "git", "status", "--porcelain", "--untracked-files=all", "--", *RELEVANT),
            "relevant git files are dirty or untracked")
    for name in NAMES:
        require(run(root, "git", "show", "HEAD:" + name) == data[name], "source not committed in HEAD")
    remotes = run(root, "git", "remote", "-v").decode().splitlines()
    urls = [line.split()[1] for line in remotes if len(line.split()) >= 2]
    require(any(re.fullmatch(r"(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)" +
                             re.escape(repo) + r"(?:\.git)?/?", url, re.I) for url in urls),
            "explicit repository is not a configured GitHub remote")
    tag = manifest["tag"]
    require(run(root, "git", "rev-parse", "--verify", f"refs/tags/{tag}^{{commit}}").decode().strip()
            == head, "local tag does not point to HEAD")
    run(root, "gh", "auth", "status", "--hostname", "github.com")
    info = gh_json(root, f"repos/{repo}")
    require(info.get("full_name", "").lower() == repo.lower() and
            info.get("permissions", {}).get("push") is True, "no push permission for explicit repo")
    obj = gh_json(root, f"repos/{repo}/git/ref/tags/{tag}")["object"]
    for _ in range(4):
        if obj.get("type") == "commit":
            break
        require(obj.get("type") == "tag" and re.fullmatch(r"[0-9a-f]{40}", obj.get("sha", "")),
                "invalid remote tag")
        obj = gh_json(root, f"repos/{repo}/git/tags/{obj['sha']}")["object"]
    require(obj.get("type") == "commit" and obj.get("sha") == head, "remote tag does not point to HEAD")
    pages = gh_json(root, f"repos/{repo}/releases?per_page=100", "--paginate", "--slurp")
    require(isinstance(pages, list) and all(isinstance(page, list) for page in pages),
            "unexpected GitHub release listing")
    require(not any(release.get("tag_name") == tag for page in pages for release in page),
            "release already exists (including drafts); refusing overwrite")
    return head


def publish(root, repo, asset, manifest_path, approved=False, confirm=None):
    repo = repository(repo)
    manifest = verify(asset, manifest_path, repo)
    tag = manifest["tag"]
    require(not approved or confirm == f"{repo}/{tag}", "--publish requires --confirm owner/repo/vVERSION")
    head = preflight(root, repo, manifest, asset)
    commands = [
        ["gh", "release", "create", tag, "--repo", "github.com/" + repo, "--draft", "--verify-tag",
         "--target", head, "--title", tag, "--notes", f"{PLUGIN} {tag}"],
        ["gh", "release", "upload", tag, str(asset.resolve()), str(manifest_path.resolve()),
         "--repo", "github.com/" + repo],
        ["gh", "release", "edit", tag, "--repo", "github.com/" + repo, "--draft=false", "--verify-tag"],
    ]
    plan = {"dryRun": not approved, "repository": repo, "tag": tag, "commit": head,
            "zip": str(asset.resolve()), "manifest": str(manifest_path.resolve()), "commands": commands,
            "betweenUploadAndPublish": "verify draft identity and downloaded asset bytes"}
    if not approved:
        return plan
    run(root, *commands[0])
    run(root, *commands[1])
    pages = gh_json(root, f"repos/{repo}/releases?per_page=100", "--paginate", "--slurp")
    drafts = [r for page in pages for r in page if r.get("tag_name") == tag]
    require(len(drafts) == 1, "remote draft missing/ambiguous; left unpublished")
    draft = drafts[0]
    require(draft.get("draft") is True and draft.get("prerelease") is False and
            draft.get("tag_name") == tag, "remote draft identity disagrees; left unpublished")
    assets = draft.get("assets", [])
    require(len(assets) == 2 and {a.get("name") for a in assets} == {asset.name, manifest_path.name},
            "remote assets disagree; draft left unpublished")
    for path in (asset, manifest_path):
        entry = next(a for a in assets if a.get("name") == path.name)
        require(type(entry.get("id")) is int and entry.get("state") == "uploaded" and
                entry.get("size") == path.stat().st_size, "remote asset metadata disagrees")
        content = run(root, "gh", "api", f"repos/{repo}/releases/assets/{entry['id']}", "--hostname",
                      "github.com", "--method", "GET", "--header", "Accept: application/octet-stream")
        require(content == read_file(path, MAX_TOTAL), "remote asset hash disagrees; draft left unpublished")
    # Fail closed if the remote tag changed while the draft was uploaded.
    remote = gh_json(root, f"repos/{repo}/commits/{tag}")
    require(remote.get("sha") == head, "remote tag changed; draft left unpublished")
    run(root, *commands[2])
    published = gh_json(root, f"repos/{repo}/releases/tags/{tag}")
    require(published.get("draft") is False and published.get("prerelease") is False and
            published.get("tag_name") == tag and sorted(
                (a.get("id"), a.get("name"), a.get("size"), a.get("state"))
                for a in published.get("assets", [])) == sorted(
                (a.get("id"), a.get("name"), a.get("size"), a.get("state")) for a in assets),
            "published release state/assets disagree; inspect release manually")
    return plan


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("build", "verify", "publish"):
        p = sub.add_parser(command)
        p.add_argument("--root", type=Path, default=Path.cwd())
        p.add_argument("--repo", required=True)
        if command == "build":
            p.add_argument("--out", type=Path, required=True, help="new output directory (must not exist)")
            p.add_argument("--version")
        else:
            p.add_argument("--zip", dest="asset", type=Path, required=True)
            p.add_argument("--manifest", type=Path, required=True)
        if command == "publish":
            group = p.add_mutually_exclusive_group()
            group.add_argument("--publish", action="store_true")
            group.add_argument("--dry-run", action="store_true")
            p.add_argument("--confirm", help="exact owner/repo/vVERSION; required with --publish")
    args = parser.parse_args(argv)
    try:
        if args.command == "build":
            result = build(args.root.resolve(), args.repo, args.out, args.version)
        elif args.command == "verify":
            result = {"zip": str(args.asset.resolve()), "manifest": str(args.manifest.resolve()),
                      **verify(args.asset, args.manifest, args.repo)}
        else:
            result = publish(args.root.resolve(), args.repo, args.asset, args.manifest, args.publish, args.confirm)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (ReleaseError, OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile) as error:
        print(f"release_tool: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
