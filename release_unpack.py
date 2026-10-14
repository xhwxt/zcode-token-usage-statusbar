#!/usr/bin/env python3
"""Validate a release asset completely before unpacking it into a new directory."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
import struct
import sys
import unicodedata
import zipfile
import zlib

PLUGIN = "zcode-token-usage-statusbar"
PREFIX = "plugins/" + PLUGIN + "/"
PLUGIN_ID = PLUGIN + "@zusage-marketplace"
MAX_JSON = 256 * 1024
MAX_ARCHIVE = 16 * 1024 * 1024
MAX_TOTAL = 64 * 1024 * 1024
MAX_FILE = 8 * 1024 * 1024
MAX_ENTRIES = 1000
MAX_RATIO = 1000
SEMVER = r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
PLUGIN_FILES = (
    "usage_mcp.py", "zusage.py", "patch_install.py", "install_monitor.py",
    "inject-main.cjs", "plugin-updater.cjs", "plugin-storage.cjs", "runtime_lock.py", "overlay.js",
    "plugin-release.cjs", "release_unpack.py", "overlay_install.py", "token_rate.py",
    "plugin_auto_update.py", "restart_notice.py", ".mcp.json", "README.md",
    ".zcode-plugin/plugin.json", ".claude-plugin/plugin.json", "hooks/hooks.json",
    "hooks/session-start.py", "hooks/prompt-submit.py", "commands/usage.md",
    "commands/install-overlay.md", "commands/uninstall-overlay.md",
    "skills/zcode-token-usage/SKILL.md",
)
ALLOWED_FILES = frozenset(("marketplace.json", "LICENSE") +
                          tuple(PREFIX + name for name in PLUGIN_FILES))
ALLOWED_DIRS = frozenset(parent for name in ALLOWED_FILES
                         for parent in ("/".join(name.split("/")[:i])
                                        for i in range(1, len(name.split("/")))))
# Official releases are complete products, not reduced test fixtures: omitted runtime
# dependencies can pass identity checks but fail when installed.
REQUIRED_FILES = ALLOWED_FILES


class UnpackError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise UnpackError(message)


def absolute_path(value):
    path = Path(value)
    require(path.is_absolute(), "archive, manifest and output paths must be absolute")
    return path


def read_bounded(path, limit):
    require(not path.is_symlink(), "input must not be a symlink: " + str(path))
    with path.open("rb") as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_size <= limit,
                "input is not a regular bounded file: " + str(path))
        data = stream.read(limit + 1)
    require(len(data) <= limit, "input exceeds size limit: " + str(path))
    return data


def json_object(data, name):
    require(len(data) <= MAX_JSON, "JSON exceeds 256KB: " + name)

    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "duplicate JSON key in " + name)
            result[key] = value
        return result

    def invalid_constant(value):
        raise UnpackError("invalid JSON constant in " + name + ": " + value)

    value = json.loads(data.decode("utf-8"), object_pairs_hook=pairs,
                       parse_constant=invalid_constant)
    require(isinstance(value, dict), "JSON must be an object: " + name)
    return value


def stable_version(value):
    require(isinstance(value, str) and len(value) <= 64 and re.fullmatch(SEMVER, value),
            "version must be a numeric three-part stable version")
    return value


def repository(value):
    require(isinstance(value, str) and len(value) <= 140 and re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9][A-Za-z0-9._-]{0,99}", value),
        "repository must be explicit owner/repo")
    return value


def release_manifest(data, archive_name, expected_repository, expected_version):
    manifest = json_object(data, "release manifest")
    require(type(manifest.get("schemaVersion")) is int and manifest["schemaVersion"] == 1,
            "unsupported release manifest schema")
    require(repository(manifest.get("repository")) == expected_repository,
            "release repository disagrees with expected source")
    require(stable_version(manifest.get("version")) == expected_version,
            "release version disagrees with expected version")
    require(manifest.get("tag") == "v" + expected_version and
            manifest.get("pluginId") == PLUGIN_ID, "release tag/plugin identity disagrees")
    if "sourceCommit" in manifest:
        commit = manifest["sourceCommit"]
        require(isinstance(commit, str) and re.fullmatch(r"[0-9a-fA-F]{40}", commit),
                "invalid sourceCommit")
    asset = manifest.get("asset")
    require(isinstance(asset, dict), "release asset must be an object")
    require(asset.get("name") == archive_name == PLUGIN + "-v" + expected_version + ".zip",
            "release asset basename disagrees")
    require(type(asset.get("size")) is int and 0 < asset["size"] <= MAX_ARCHIVE,
            "release asset size exceeds bound or is invalid")
    digest = asset.get("sha256")
    require(isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest),
            "invalid release asset sha256")
    return asset


def member_path(member):
    name = member.orig_filename
    require(name == member.filename and name and name.isascii() and
            "\\" not in name and ":" not in name and "\x00" not in name,
            "unsafe ZIP path: " + repr(name))
    path = name[:-1] if name.endswith("/") else name
    parts = path.split("/")
    for part in parts:
        require(part not in ("", ".", "..") and not part.endswith((".", " ")) and
                not any(ord(char) < 32 or char in '<>"|?*' for char in part),
                "unsafe ZIP component: " + repr(name))
        require(not re.fullmatch(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])",
                                 part.split(".", 1)[0], re.I),
                "Windows device path: " + repr(name))
    return path


def declared_resources(manifest, data):
    for component in ("commands", "skills"):
        values = manifest.get(component)
        values = [values] if isinstance(values, str) else values
        require(isinstance(values, list) and values, "missing declared " + component)
        for value in values:
            require(isinstance(value, str), "invalid declared " + component)
            relative = value[2:] if value.startswith("./") else value
            require(relative == component or relative.startswith(component + "/"),
                    "unsafe declared " + component + " path")
            name = PREFIX + relative
            require(name in ALLOWED_FILES or name in ALLOWED_DIRS,
                    "non-whitelisted declared resource: " + value)
            if name in ALLOWED_FILES:
                require(name in data and (name.endswith(".md") if component == "commands"
                                         else name.endswith("/SKILL.md")),
                        "missing declared resource: " + value)
            else:
                suffix = ".md" if component == "commands" else "/SKILL.md"
                require(any(key.startswith(name + "/") and key.endswith(suffix) for key in data),
                        "empty declared resource directory: " + value)


def referenced_resources(value, data):
    if isinstance(value, dict):
        for item in value.values():
            referenced_resources(item, data)
    elif isinstance(value, list):
        for item in value:
            referenced_resources(item, data)
    elif isinstance(value, str) and "${ZCODE_PLUGIN_ROOT}" in value:
        for reference in re.findall(r"\$\{ZCODE_PLUGIN_ROOT\}([^\s\"']*)", value):
            require(reference.startswith("/") and PREFIX + reference[1:] in data,
                    "missing or unsafe declared plugin resource: " + reference)


def identities(data, version):
    market = json_object(data["marketplace.json"], "marketplace.json")
    plugin = json_object(data[PREFIX + ".zcode-plugin/plugin.json"], "plugin manifest")
    compatibility = json_object(data[PREFIX + ".claude-plugin/plugin.json"], "compatibility manifest")
    require(json.dumps(plugin, sort_keys=True) == json.dumps(compatibility, sort_keys=True),
            "plugin manifests disagree")
    require(plugin.get("name") == PLUGIN and stable_version(plugin.get("version")) == version,
            "plugin name/version disagrees")
    require(market.get("name") == "zusage-marketplace", "marketplace identity disagrees")
    entries = market.get("plugins")
    require(isinstance(entries, list) and len(entries) == 1 and isinstance(entries[0], dict),
            "marketplace must contain exactly one plugin")
    entry = entries[0]
    require(entry.get("name") == PLUGIN and entry.get("version") == version and
            entry.get("source") in ("./plugins/" + PLUGIN, "plugins/" + PLUGIN),
            "marketplace plugin name/version/source disagrees")
    declared_resources(plugin, data)
    for name, content in data.items():
        if name.endswith(".json"):
            referenced_resources(json_object(content, name), data)


def local_member(payload, member):
    offset = member.header_offset
    require(0 <= offset <= len(payload) - 30, "invalid ZIP local header offset")
    require(payload[offset:offset + 4] == b"PK\x03\x04", "invalid ZIP local header")
    flags, method = struct.unpack_from("<HH", payload, offset + 6)
    crc, compressed, size = struct.unpack_from("<III", payload, offset + 14)
    name_length, extra_length = struct.unpack_from("<HH", payload, offset + 26)
    start = offset + 30 + name_length + extra_length
    require(start + member.compress_size <= len(payload), "ZIP compressed range exceeds archive")
    local_name = payload[offset + 30:offset + 30 + name_length]
    require(local_name == member.filename.encode("ascii") and flags == member.flag_bits and
            method == member.compress_type, "ZIP local header name/flags/compression disagrees")
    if not flags & 8:
        require((crc, compressed, size) == (member.CRC, member.compress_size, member.file_size),
                "ZIP local header CRC/size disagrees")
    if method == zipfile.ZIP_DEFLATED:
        decoder = zlib.decompressobj(-15)
        content = decoder.decompress(payload[start:start + member.compress_size], MAX_FILE + 1)
        require(decoder.eof and not decoder.unused_data and not decoder.unconsumed_tail and
                len(content) == member.file_size, "ZIP deflate stream/size disagrees")


def validate_zip(payload, version):
    data, seen, total = {}, set(), 0
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        members = archive.infolist()
        require(len(members) <= MAX_ENTRIES, "ZIP entry count exceeds limit")
        for member in members:
            path = member_path(member)
            canonical = unicodedata.normalize("NFC", path).casefold()
            require(canonical not in seen, "duplicate or colliding ZIP path: " + path)
            seen.add(canonical)
            is_dir = member.is_dir()
            require(path in (ALLOWED_DIRS if is_dir else ALLOWED_FILES),
                    "non-whitelisted ZIP path: " + path)
            mode = stat.S_IFMT(member.external_attr >> 16)
            require(mode in (0, stat.S_IFDIR if is_dir else stat.S_IFREG) and
                    (is_dir or not member.external_attr & 0x10),
                    "ZIP contains a symlink/device or invalid directory")
            require(not member.flag_bits & 0x41 and member.compress_type in
                    (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED),
                    "encrypted or unsupported ZIP compression")
            require(0 <= member.file_size <= MAX_FILE and
                    0 <= member.compress_size <= len(payload), "ZIP file size exceeds limit")
            require(member.compress_type != zipfile.ZIP_STORED or
                    member.compress_size == member.file_size, "stored ZIP size disagrees")
            require(member.file_size <= max(member.compress_size, 1) * MAX_RATIO,
                    "ZIP compression ratio exceeds limit")
            require(not is_dir or member.file_size == 0, "nonempty ZIP directory")
            require(not path.endswith(".json") or member.file_size <= MAX_JSON,
                    "ZIP JSON exceeds 256KB")
            total += member.file_size
            require(total <= MAX_TOTAL, "ZIP extracted total exceeds limit")
        files = {member_path(member) for member in members if not member.is_dir()}
        require(files == REQUIRED_FILES, "ZIP is missing required release files")
        # Read every member (including directories) to validate CRC and advertised size.
        # No directory or package code is touched until every member and identity is valid.
        for member in members:
            local_member(payload, member)
            with archive.open(member) as stream:
                content = stream.read(MAX_FILE + 1)
                require(len(content) == member.file_size and not stream.read(1),
                        "ZIP actual size disagrees: " + member.filename)
            if not member.is_dir():
                data[member.filename] = content
    identities(data, version)
    return data


def unpack(archive, manifest, expected_repository, expected_version, output):
    archive, manifest, output = map(absolute_path, (archive, manifest, output))
    expected_repository = repository(expected_repository)
    expected_version = stable_version(expected_version)
    require(not os.path.lexists(str(output)), "output directory already exists")
    record = release_manifest(read_bounded(manifest, MAX_JSON), archive.name,
                              expected_repository, expected_version)
    payload = read_bounded(archive, MAX_ARCHIVE)
    require(len(payload) == record["size"] and hashlib.sha256(payload).hexdigest() == record["sha256"],
            "archive size/sha256 disagrees")
    data = validate_zip(payload, expected_version)
    # mkdir(exist_ok=False) also protects against another writer creating output during validation.
    output.mkdir()
    owned = output.stat()
    try:
        for name, content in data.items():
            destination = output / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("xb") as stream:
                stream.write(content)
    except BaseException:
        try:
            current = output.lstat()
            if (current.st_dev, current.st_ino) == (owned.st_dev, owned.st_ino):
                shutil.rmtree(output)
        except OSError:
            pass
        raise
    return {"ok": True, "marketplaceRoot": str(output.resolve()),
            "pluginRoot": str((output / "plugins" / PLUGIN).resolve()),
            "version": expected_version}


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise UnpackError(message)


def main(argv=None):
    try:
        parser = Parser(description=__doc__)
        parser.add_argument("--archive", required=True)
        parser.add_argument("--manifest", required=True)
        parser.add_argument("--repository", required=True)
        parser.add_argument("--version", required=True)
        parser.add_argument("--output", required=True)
        args = parser.parse_args(argv)
        result = unpack(args.archive, args.manifest, args.repository, args.version, args.output)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (ValueError, OSError, TypeError, RuntimeError, EOFError, RecursionError,
            zipfile.BadZipFile, zipfile.LargeZipFile, zlib.error) as error:
        print("release_unpack: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
