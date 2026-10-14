# -*- coding: utf-8 -*-
"""Permanent-file OS locks shared by the runtime synchronizer and Node helpers."""
import argparse
import errno
import json
import os
import stat
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def file_lock(path, timeout=10.0):
    """Acquire an advisory lock or raise TimeoutError; never unlink the lock file."""
    fd = os.open(os.fspath(path), os.O_RDWR | os.O_CREAT, 0o600)
    locked = False
    deadline = time.monotonic() + max(0.0, timeout)
    try:
        if os.name == "nt":
            import msvcrt
            if os.fstat(fd).st_size == 0:
                os.write(fd, b"\0")
        else:
            import fcntl
        while True:
            try:
                if os.name == "nt":
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
                break
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                    raise
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("runtime lock is busy: " + os.fspath(path)) from exc
                time.sleep(min(0.05, remaining))
        yield
    finally:
        try:
            if locked:
                if os.name == "nt":
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _read_marker(marker):
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _hashes(value):
    if not isinstance(value, dict):
        return {}
    return {key: digest for key, digest in value.items()
            if isinstance(key, str) and not key.startswith("_")
            and isinstance(digest, str) and digest}


def _write_marker(marker, data, preserve=None):
    fd, name = tempfile.mkstemp(prefix=marker.name + ".", suffix=".tmp", dir=marker.parent)
    try:
        if preserve is not None:
            if os.name != "nt":
                current = os.fstat(fd)
                if (current.st_uid, current.st_gid) != (preserve.st_uid, preserve.st_gid):
                    os.fchown(fd, preserve.st_uid, preserve.st_gid)
            os.chmod(name, stat.S_IMODE(preserve.st_mode))
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            fd = None
            json.dump(data, stream, ensure_ascii=False)
            stream.write("\n")
        os.replace(name, marker)
    finally:
        if fd is not None:
            os.close(fd)
        try:
            os.unlink(name)
        except FileNotFoundError:
            pass


def update_config(runtime, changes, defaults=None):
    """Merge only the requested keys after rereading config under its shared lock."""
    if not isinstance(changes, dict) or (defaults is not None and not isinstance(defaults, dict)):
        raise ValueError("config changes/defaults must be JSON objects")
    runtime = Path(runtime)
    runtime.mkdir(parents=True, exist_ok=True)
    config = runtime / "config.json"
    lock_path = runtime / "plugin-config.lock"
    with file_lock(lock_path):
        owner = config.stat() if config.exists() else runtime.stat()
        if os.name != "nt" and hasattr(os, "geteuid") and os.geteuid() == 0:
            lock_stat = lock_path.stat()
            if (lock_stat.st_uid, lock_stat.st_gid) != (owner.st_uid, owner.st_gid):
                os.chown(lock_path, owner.st_uid, owner.st_gid)
        try:
            current = json.loads(config.read_text(encoding="utf-8"))
        except FileNotFoundError:
            current = {}
        if not isinstance(current, dict):
            raise ValueError("config.json must be a JSON object")
        updated = dict(current)
        for key, value in (defaults or {}).items():
            updated.setdefault(key, value)
        updated.update(changes)
        if updated == current and config.exists():
            return False
        _write_marker(config, updated, config.stat() if config.exists() else None)
        return True


def record_restart(runtime, data):
    """Merge a restart notification while holding the independent marker lock."""
    if not isinstance(data, dict):
        raise ValueError("restart data must be a JSON object")
    runtime = Path(runtime)
    marker = runtime / ".plugin-restart-required"
    with file_lock(runtime / "plugin-restart.lock"):
        previous = _read_marker(marker)
        hashes = _hashes(previous.get("hashes"))
        hashes.update(_hashes(data.get("hashes")))
        previous.update(data)
        previous.update(createdAt=time.time(), hashes=hashes)
        _write_marker(marker, previous)
    return {"ok": True}


def ack_restart(runtime, loaded):
    """Remove only hashes actually loaded; preserve newer legacy notifications."""
    if not isinstance(loaded, dict):
        raise ValueError("loaded hashes must be a JSON object")
    runtime = Path(runtime)
    marker = runtime / ".plugin-restart-required"
    with file_lock(runtime / "plugin-restart.lock"):
        if not marker.exists():
            return {"ok": True, "cleared": False}
        data = _read_marker(marker)
        loaded_at = loaded.get("_loadedAt")
        created_at = data.get("createdAt")
        if not isinstance(created_at, (int, float)) or isinstance(created_at, bool):
            created_at = marker.stat().st_mtime
        if isinstance(loaded_at, (int, float)) and created_at * 1000 > loaded_at:
            return {"ok": True, "cleared": False}
        hashes = _hashes(data.get("hashes"))
        if hashes:
            remaining = {key: digest for key, digest in hashes.items() if loaded.get(key) != digest}
            if remaining:
                data["hashes"] = remaining
                _write_marker(marker, data)
                return {"ok": True, "cleared": False}
        marker.unlink()
    return {"ok": True, "cleared": True}


def main():
    parser = argparse.ArgumentParser()
    operation = parser.add_mutually_exclusive_group(required=True)
    operation.add_argument("--hold", metavar="LOCKPATH")
    operation.add_argument("--record-restart", metavar="RUNTIME")
    operation.add_argument("--ack-restart", nargs=2, metavar=("RUNTIME", "HASHES_JSON"))
    operation.add_argument("--update-config", metavar="RUNTIME")
    args = parser.parse_args()
    if args.hold:
        try:
            with file_lock(args.hold, timeout=0):
                print(json.dumps({"locked": True}, separators=(",", ":")), flush=True)
                while sys.stdin.buffer.read(65536):
                    pass
        except TimeoutError:
            print(json.dumps({"locked": False}, separators=(",", ":")), flush=True)
    elif args.record_restart:
        print(json.dumps(record_restart(args.record_restart, json.load(sys.stdin))))
    elif args.update_config:
        data = json.load(sys.stdin)
        if not isinstance(data, dict) or "changes" not in data:
            raise ValueError("config update must contain changes")
        changed = update_config(args.update_config, data["changes"], data.get("defaults"))
        print(json.dumps({"ok": True, "changed": changed}))
    else:
        print(json.dumps(ack_restart(args.ack_restart[0], json.loads(args.ack_restart[1]))))


if __name__ == "__main__":
    main()
