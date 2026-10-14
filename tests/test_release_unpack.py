# -*- coding: utf-8 -*-
"""Release validation uses only synthetic archives and isolated temporary directories."""
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import warnings
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("release_unpack_test", ROOT / "release_unpack.py")
unpacker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(unpacker)
PLUGIN = unpacker.PLUGIN
PREFIX = unpacker.PREFIX
VERSION = "1.4.0"
REPOSITORY = "owner/repo"


def encoded(value):
    return json.dumps(value).encode("utf-8")


class ReleaseUnpackTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.archive = self.root / (PLUGIN + "-v" + VERSION + ".zip")
        self.manifest_path = self.root / "release-manifest.json"
        self.output = self.root / "unpacked"
        # Deliberately invalid Python/JS: validation must never execute package code.
        self.data = {name: b"not executable package code\n" for name in unpacker.ALLOWED_FILES}
        self.data["LICENSE"] = b"MIT License\n"
        self.plugin_manifest = {"name": PLUGIN, "version": VERSION,
                                "commands": "commands", "skills": "skills/zcode-token-usage"}
        self.market = {"name": "zusage-marketplace", "plugins": [
            {"name": PLUGIN, "version": VERSION, "source": "./plugins/" + PLUGIN}]}
        self.data["marketplace.json"] = encoded(self.market)
        self.set_plugin_manifest(self.plugin_manifest)
        self.data[PREFIX + ".mcp.json"] = encoded({"mcpServers": {}})
        self.data[PREFIX + "hooks/hooks.json"] = encoded({"hooks": {}})
        self.data[PREFIX + "commands/usage.md"] = b"usage command"
        self.data[PREFIX + "skills/zcode-token-usage/SKILL.md"] = b"usage skill"
        self.pack()

    def set_plugin_manifest(self, value):
        for name in (".zcode-plugin/plugin.json", ".claude-plugin/plugin.json"):
            self.data[PREFIX + name] = encoded(value)

    def refresh_manifest(self):
        payload = self.archive.read_bytes()
        self.release = {"schemaVersion": 1, "repository": REPOSITORY, "version": VERSION,
                        "tag": "v" + VERSION, "pluginId": unpacker.PLUGIN_ID,
                        "asset": {"name": self.archive.name, "size": len(payload),
                                  "sha256": hashlib.sha256(payload).hexdigest()}}
        self.write_manifest()

    def write_manifest(self):
        self.manifest_path.write_bytes(encoded(self.release))

    def pack(self, extras=(), compression=zipfile.ZIP_STORED):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(self.archive, "w", compression=compression) as archive:
                for name, value in sorted(self.data.items()):
                    archive.writestr(name, value)
                for name, value in extras:
                    archive.writestr(name, value)
        self.refresh_manifest()

    def args(self):
        return ["--archive", str(self.archive), "--manifest", str(self.manifest_path),
                "--repository", REPOSITORY, "--version", VERSION, "--output", str(self.output)]

    def run_cli(self, args=None):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = unpacker.main(self.args() if args is None else args)
        return code, out.getvalue(), err.getvalue()

    def assert_rejected(self, args=None, contains=None):
        code, out, err = self.run_cli(args)
        self.assertEqual(code, 1, (out, err))
        self.assertEqual(out, "")
        self.assertTrue(err)
        if contains:
            self.assertIn(contains, err)
        self.assertFalse(os.path.lexists(str(self.output)))
        self.assertEqual(sorted(path.name for path in self.root.iterdir()),
                         sorted([self.archive.name, self.manifest_path.name]))

    def patch_zip(self, local_offset, central_offset, value, fmt="I", name="LICENSE"):
        payload = bytearray(self.archive.read_bytes())
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            info = archive.getinfo(name)
        struct.pack_into("<" + fmt, payload, info.header_offset + local_offset, value)
        offset = payload.find(b"PK\x01\x02")
        while offset >= 0:
            name_len, extra_len, comment_len = struct.unpack_from("<HHH", payload, offset + 28)
            member_name = bytes(payload[offset + 46:offset + 46 + name_len]).decode("utf-8")
            if member_name == name:
                struct.pack_into("<" + fmt, payload, offset + central_offset, value)
                break
            offset = payload.find(b"PK\x01\x02", offset + 46 + name_len + extra_len + comment_len)
        self.assertGreaterEqual(offset, 0)
        self.archive.write_bytes(payload)
        self.refresh_manifest()

    def test_cli_success_and_canonical_absolute_paths_without_executing_code(self):
        result = subprocess.run([sys.executable, "-B", str(ROOT / "release_unpack.py"), *self.args()],
                                cwd=str(self.root), capture_output=True, text=True, check=False,
                                env=dict(os.environ, HOME=str(self.root), PYTHONDONTWRITEBYTECODE="1"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), {
            "ok": True, "marketplaceRoot": str(self.output.resolve()),
            "pluginRoot": str((self.output / "plugins" / PLUGIN).resolve()), "version": VERSION})
        actual = {str(path.relative_to(self.output)): path.read_bytes()
                  for path in self.output.rglob("*") if path.is_file()}
        self.assertEqual(actual, self.data)

    def test_stored_deflated_and_whitelisted_explicit_directories(self):
        directories = [(name + "/", b"") for name in sorted(unpacker.ALLOWED_DIRS)]
        self.pack(directories, zipfile.ZIP_DEFLATED)
        self.assertEqual(self.run_cli()[0], 0)

    def test_asset_tamper_rejected_before_output_creation(self):
        self.archive.write_bytes(self.archive.read_bytes() + b"tamper")
        self.assert_rejected(contains="size/sha256")

    def test_same_size_hash_tamper(self):
        payload = bytearray(self.archive.read_bytes())
        payload[50] ^= 1
        self.archive.write_bytes(payload)
        self.assert_rejected(contains="size/sha256")

    def test_release_manifest_identity_types_bounds_and_schema(self):
        original = copy.deepcopy(self.release)
        invalid = [("schemaVersion", True), ("schemaVersion", 2), ("schemaVersion", "1"),
                   ("repository", "upstream/repo"), ("repository", "https://github.com/owner/repo"),
                   ("version", "1.3.9"), ("version", "1.4.0-beta"), ("tag", "v1.3.9"),
                   ("pluginId", PLUGIN + "@other"), ("asset", []),
                   ("sourceCommit", "x" * 40), ("sourceCommit", None)]
        for key, value in invalid:
            with self.subTest(key=key, value=value):
                self.release = copy.deepcopy(original)
                self.release[key] = value
                self.write_manifest()
                self.assert_rejected()
        for key, value in (("size", True), ("size", -1), ("size", 0),
                           ("size", unpacker.MAX_ARCHIVE + 1), ("size", 10**100),
                           ("sha256", "a" * 63), ("sha256", "A" * 64),
                           ("name", "../" + self.archive.name), ("name", "other.zip")):
            with self.subTest(asset_key=key, value=value):
                self.release = copy.deepcopy(original)
                self.release["asset"][key] = value
                self.write_manifest()
                self.assert_rejected()

    def test_optional_source_commit_is_not_required(self):
        self.release["sourceCommit"] = "abcdef0123" * 4
        self.write_manifest()
        self.assertEqual(self.run_cli()[0], 0)

    def test_expected_version_rejects_downgrade_and_unstable_versions(self):
        for version in ("1.4.1", "1.4.0-rc.1", "1.4.0+build", "01.4.0", "1.4", "v1.4.0"):
            with self.subTest(version=version):
                args = self.args()
                args[args.index("--version") + 1] = version
                self.assert_rejected(args)

    def test_exact_expected_source_and_archive_basename(self):
        args = self.args()
        args[args.index("--repository") + 1] = "another/repo"
        self.assert_rejected(args)
        wrong = self.root / "renamed.zip"
        self.archive.rename(wrong)
        args = self.args()
        args[args.index("--archive") + 1] = str(wrong)
        code, _, err = self.run_cli(args)
        self.assertEqual(code, 1)
        self.assertIn("basename", err)
        self.assertFalse(self.output.exists())

    def test_paths_must_be_absolute_and_cli_errors_exit_one(self):
        for option in ("--archive", "--manifest", "--output"):
            args = self.args()
            args[args.index(option) + 1] = "relative"
            self.assert_rejected(args)
        self.assert_rejected([])
        self.assert_rejected(self.args() + ["--unknown"])

    def test_release_manifest_json_size_shape_and_duplicate_keys(self):
        for data in (b" " * (unpacker.MAX_JSON + 1), b"[]", b"null", b"{bad", b"\xff",
                     b'{"schemaVersion":1,"schemaVersion":1}', b'{"schemaVersion":NaN}'):
            with self.subTest(data=data[:50]):
                self.manifest_path.write_bytes(data)
                self.assert_rejected()

    def test_archive_and_json_physical_limits_are_not_manifest_controlled(self):
        with self.archive.open("ab") as stream:
            stream.truncate(unpacker.MAX_ARCHIVE + 1)
        self.assert_rejected()

    def test_traversal_absolute_backslash_unicode_and_windows_paths(self):
        paths = ["../escape", "/absolute", "C:/escape", "\\absolute", PREFIX + "../secret",
                 PREFIX + "hooks/../../escape", PREFIX + "./zusage.py", PREFIX + "hooks//file",
                 PREFIX + "zusage.py:secret", PREFIX + "CON.txt", PREFIX + "LPT9.py",
                 PREFIX + "hooks/NUL", PREFIX + "zusage.py.", PREFIX + "zusage.py ",
                 PREFIX + "caf\u00e9.py", PREFIX + "cafe\u0301.py", PREFIX + "foo\x00bar",
                 PREFIX + "tab\tfile", "plugins/other/readme.md"]
        for path in paths:
            with self.subTest(path=path):
                # ZipInfo truncates NUL names; they remain outside the exact whitelist.
                self.pack([(path, b"bad")])
                self.assert_rejected()

    def test_duplicates_casefold_and_file_directory_collisions(self):
        for name in ("LICENSE", "license", PREFIX + "ZUSAGE.PY", "marketplace.json/",
                     "plugins", PREFIX + "hooks/hooks.json/"):
            with self.subTest(name=name):
                self.pack([(name, b"")])
                self.assert_rejected()

    def test_symlink_device_fifo_socket_and_mode_mismatches(self):
        del self.data[PREFIX + "README.md"]
        for mode in (stat.S_IFLNK, stat.S_IFCHR, stat.S_IFBLK, stat.S_IFIFO,
                     stat.S_IFSOCK, stat.S_IFDIR):
            with self.subTest(mode=mode):
                info = zipfile.ZipInfo(PREFIX + "README.md")
                info.create_system = 3
                info.external_attr = (mode | 0o644) << 16
                self.pack([(info, b"target")])
                self.assert_rejected()

    def test_encrypted_strong_encryption_and_unsupported_compression(self):
        for flag in (1, 0x40):
            self.pack()
            self.patch_zip(6, 8, flag, "H")
            self.assert_rejected(contains="encrypted")
        for method in (zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA, 99):
            self.pack()
            self.patch_zip(8, 10, method, "H")
            self.assert_rejected(contains="compression")

    def test_crc_and_forged_uncompressed_size(self):
        self.patch_zip(14, 16, 0)
        self.assert_rejected()
        self.pack()
        self.patch_zip(22, 24, 1)
        self.assert_rejected()

    def test_compression_ratio_bomb(self):
        self.data[PREFIX + "README.md"] = b"0" * (2 * 1024 * 1024)
        self.pack(compression=zipfile.ZIP_DEFLATED)
        self.assert_rejected(contains="ratio")

    def test_deflated_hidden_size_and_local_header_mismatch(self):
        self.pack(compression=zipfile.ZIP_DEFLATED)
        self.patch_zip(22, 24, 1)
        self.assert_rejected(contains="deflate stream/size")
        self.pack()
        payload = bytearray(self.archive.read_bytes())
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            offset = archive.getinfo("LICENSE").header_offset
        struct.pack_into("<H", payload, offset + 6, 1)
        self.archive.write_bytes(payload)
        self.refresh_manifest()
        self.assert_rejected(contains="local header")

    def test_single_file_and_json_entry_limits(self):
        self.patch_zip(22, 24, unpacker.MAX_FILE + 1)
        self.assert_rejected(contains="file size")
        self.pack()
        self.patch_zip(22, 24, unpacker.MAX_JSON + 1, name="marketplace.json")
        self.assert_rejected()

    def test_total_and_entry_count_bounds_checked_before_decompression(self):
        with mock.patch.object(unpacker, "MAX_TOTAL", sum(map(len, self.data.values())) - 1):
            self.assert_rejected(contains="total")
        with mock.patch.object(unpacker, "MAX_ENTRIES", len(self.data) - 1):
            self.assert_rejected(contains="count")
        self.pack([(PREFIX + "README.md", b"")] * 1001)
        self.assert_rejected(contains="count")

    def test_ratio_boundary_allows_1000(self):
        self.patch_zip(8, 10, zipfile.ZIP_DEFLATED, "H")
        self.patch_zip(18, 20, 1)
        self.patch_zip(22, 24, 1000)
        with mock.patch.object(unpacker, "local_member"), \
                mock.patch.object(zipfile.ZipFile, "open", side_effect=RuntimeError("passed scan")):
            self.assert_rejected(contains="passed scan")
        self.patch_zip(22, 24, 1001)
        self.assert_rejected(contains="ratio")

    def test_untrusted_runtime_configs_caches_and_secrets_not_packaged(self):
        for relative in ("config.json", "usage.json", "usage.db", "runtime.lock", "foo.lock",
                         "__pycache__/foo.pyc", ".git/config", ".DS_Store", ".env", "key.pem",
                         "key.key", "private.p12", "secret.json", "hooks/config.json"):
            with self.subTest(relative=relative):
                self.pack([(PREFIX + relative, b"secret")])
                self.assert_rejected(contains="whitelisted")

    def test_complete_release_whitelist_is_required_including_runtime_unpacker(self):
        self.assertEqual(unpacker.REQUIRED_FILES, unpacker.ALLOWED_FILES)
        self.assertEqual(set(self.data), unpacker.ALLOWED_FILES)
        self.assertIn(PREFIX + "release_unpack.py", self.data)
        self.assertIn(PREFIX + "skills/zcode-token-usage/SKILL.md", self.data)
        original = self.data.copy()
        for name in sorted(unpacker.ALLOWED_FILES):
            with self.subTest(name=name):
                self.data = original.copy()
                del self.data[name]
                self.pack()
                self.assert_rejected(contains="missing required release files")

    def test_maintainer_release_skill_paths_are_not_allowed(self):
        for relative in ("skills/github-release/SKILL.md",
                         "skills/github-release/references/release-contract.md",
                         "skills/github-release/scripts/release_tool.py",
                         "skills/github-release/scripts/release_unpack.py"):
            with self.subTest(relative=relative):
                name = PREFIX + relative
                self.assertNotIn(relative, unpacker.PLUGIN_FILES)
                self.assertNotIn(name, unpacker.ALLOWED_FILES)
                self.assertNotIn(name, unpacker.REQUIRED_FILES)
                self.assertNotIn(name, self.data)
                self.pack([(name, b"maintainer-only skill")])
                self.assert_rejected(contains="non-whitelisted ZIP path")
        for relative in ("skills/github-release", "skills/github-release/references",
                         "skills/github-release/scripts"):
            with self.subTest(directory=relative):
                self.assertNotIn(PREFIX + relative, unpacker.ALLOWED_DIRS)
                self.pack([(PREFIX + relative + "/", b"")])
                self.assert_rejected(contains="non-whitelisted ZIP path")
        for relative in ("skills/github-release", "skills/github-release/SKILL.md"):
            with self.subTest(declared_skill=relative):
                self.set_plugin_manifest(dict(self.plugin_manifest, skills=[relative]))
                self.pack()
                self.assert_rejected(contains="non-whitelisted declared resource")

    def test_catalog_and_two_plugin_manifests_identity_mismatches(self):
        original = self.data.copy()
        bad_markets = [dict(self.market, name="other"), dict(self.market, plugins=[]),
                       dict(self.market, plugins=self.market["plugins"] * 2),
                       dict(self.market, plugins=[None])]
        for field, value in (("name", "other"), ("version", "1.3.9"), ("source", "./plugins/other"),
                             ("source", "./plugins/" + PLUGIN + "/"),
                             ("source", "./plugins/./" + PLUGIN), ("source", "../plugins/" + PLUGIN)):
            entry = dict(self.market["plugins"][0], **{field: value})
            bad_markets.append(dict(self.market, plugins=[entry]))
        for market in bad_markets:
            with self.subTest(market=market):
                self.data = original.copy()
                self.data["marketplace.json"] = encoded(market)
                self.pack()
                self.assert_rejected()
        for name, changes in ((".claude-plugin/plugin.json", {"description": "different"}),
                              (".zcode-plugin/plugin.json", {"version": "1.3.9"})):
            self.data = original.copy()
            self.data[PREFIX + name] = encoded(dict(self.plugin_manifest, **changes))
            self.pack()
            self.assert_rejected()
        for changes in ({"name": "other"}, {"version": "1.3.9"}, {"version": True},
                        {"commands": "../commands"}, {"skills": "/skills"},
                        {"commands": "commands/missing.md"}, {"skills": "skills/missing"}):
            self.data = original.copy()
            self.set_plugin_manifest(dict(self.plugin_manifest, **changes))
            self.pack()
            self.assert_rejected()

    def test_plugin_manifests_are_strictly_type_equal(self):
        self.set_plugin_manifest(dict(self.plugin_manifest, enabled=True))
        self.data[PREFIX + ".claude-plugin/plugin.json"] = encoded(dict(self.plugin_manifest, enabled=1))
        self.pack()
        self.assert_rejected(contains="manifests disagree")

    def test_safe_normalized_source_and_declared_resource_list(self):
        self.market["plugins"][0]["source"] = "plugins/" + PLUGIN
        self.data["marketplace.json"] = encoded(self.market)
        self.set_plugin_manifest(dict(self.plugin_manifest, commands=["./commands/usage.md"],
                                      skills=["skills/zcode-token-usage"]))
        self.pack()
        self.assertEqual(self.run_cli()[0], 0)

    def test_invalid_packaged_json_rejected_without_partial_extraction(self):
        for name in ("marketplace.json", PREFIX + ".zcode-plugin/plugin.json",
                     PREFIX + ".claude-plugin/plugin.json", PREFIX + ".mcp.json",
                     PREFIX + "hooks/hooks.json"):
            original = self.data.copy()
            for content in (b"[]", b"{bad", b"\xff", b'{"same":1,"same":2}'):
                with self.subTest(name=name, content=content):
                    self.data = original.copy()
                    self.data[name] = content
                    self.pack()
                    self.assert_rejected()
            self.data = original

    def test_declared_hook_and_mcp_resource_paths_must_exist_and_be_safe(self):
        for name in (PREFIX + "hooks/hooks.json", PREFIX + ".mcp.json"):
            for reference in ("${ZCODE_PLUGIN_ROOT}/missing.py",
                              "${ZCODE_PLUGIN_ROOT}/hooks/../zusage.py",
                              "${ZCODE_PLUGIN_ROOT}/hooks\\session-start.py"):
                with self.subTest(name=name, reference=reference):
                    self.data[name] = encoded({"nested": [{"args": [reference]}]})
                    self.pack()
                    self.assert_rejected(contains="declared plugin resource")
        self.data[PREFIX + "hooks/hooks.json"] = encoded({"hooks": {
            "SessionStart": [{"args": ["${ZCODE_PLUGIN_ROOT}/hooks/session-start.py"]}]}})
        self.data[PREFIX + ".mcp.json"] = encoded({"mcpServers": {
            PLUGIN: {"args": ["${ZCODE_PLUGIN_ROOT}/usage_mcp.py"]}}})
        self.pack()
        self.assertEqual(self.run_cli()[0], 0)

    def test_existing_empty_output_directory_is_not_removed(self):
        self.output.mkdir()
        code, out, err = self.run_cli()
        self.assertEqual((code, out), (1, ""))
        self.assertIn("already exists", err)
        self.assertTrue(self.output.is_dir())

    def test_existing_output_files_and_symlinks_are_not_touched(self):
        for kind in ("file", "directory", "symlink", "dangling"):
            with self.subTest(kind=kind):
                if kind == "file":
                    self.output.write_bytes(b"unknown data")
                elif kind == "directory":
                    self.output.mkdir()
                    (self.output / "config.json").write_bytes(b"unknown config")
                else:
                    self.output.symlink_to(self.archive if kind == "symlink" else self.root / "missing")
                code, out, err = self.run_cli()
                self.assertEqual((code, out), (1, ""))
                self.assertIn("already exists", err)
                self.assertTrue(os.path.lexists(str(self.output)))
                if kind == "directory":
                    self.assertEqual((self.output / "config.json").read_bytes(), b"unknown config")
                    (self.output / "config.json").unlink()
                    self.output.rmdir()
                else:
                    self.output.unlink()

    def test_invalid_zip_and_input_symlinks(self):
        self.archive.write_bytes(b"not a ZIP")
        self.refresh_manifest()
        self.assert_rejected()
        self.pack()
        saved = self.root / "saved.zip"
        self.archive.rename(saved)
        self.archive.symlink_to(saved)
        code, _, err = self.run_cli()
        self.assertEqual(code, 1)
        self.assertIn("symlink", err)
        self.assertFalse(self.output.exists())

    def test_write_failure_removes_only_owned_output(self):
        real_open = Path.open

        def fail_second_write(path, mode="r", *args, **kwargs):
            if mode == "xb" and path.name == "marketplace.json":
                raise OSError("isolated simulated write failure")
            return real_open(path, mode, *args, **kwargs)

        with mock.patch.object(Path, "open", fail_second_write):
            self.assert_rejected(contains="write failure")

    def test_output_created_concurrently_after_validation_is_not_removed(self):
        validate = unpacker.validate_zip

        def concurrent_output(payload, version):
            result = validate(payload, version)
            self.output.mkdir()
            (self.output / "unknown").write_bytes(b"keep")
            return result

        with mock.patch.object(unpacker, "validate_zip", side_effect=concurrent_output):
            code, _, err = self.run_cli()
        self.assertEqual(code, 1)
        self.assertTrue(err)
        self.assertEqual((self.output / "unknown").read_bytes(), b"keep")


if __name__ == "__main__":
    unittest.main()
