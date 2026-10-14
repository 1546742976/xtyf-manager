#!/usr/bin/env python3
"""Offline, verified recovery-point archive for the complete xtyf-manager data set.

No online consistency claims: the operator must stop *all* application writers.
All outputs contain passwords, invoices and signing keys: keep archives private.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import tempfile
import zipfile
from datetime import datetime, timezone
from contextlib import closing
from pathlib import Path, PurePosixPath

DATABASES = ("data.db", "data/funds/funds.db")
REQUIRED = (*DATABASES, "secret.key")
DIRECTORIES = ("data/funds", "data/invoices", "data/task_attachments")
SKIP = {"data/funds/.funds-ledger.lock", "data/invoices/.invoice.lock"}
VERSION = 1
MANIFEST = "snapshot-manifest.json"


class SnapshotError(RuntimeError):
    pass


def service_is_active() -> bool:
    try:
        return subprocess.run(
            ["systemctl", "is-active", "--quiet", "rm-progress"],
            check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        ).returncode == 0
    except FileNotFoundError:
        return False


def _valid_member(name: str) -> bool:
    if not isinstance(name, str) or not name or "\\" in name:
        return False
    parts = PurePosixPath(name).parts
    if name.startswith("/") or any(part in ("", ".", "..") for part in name.split("/")):
        return False
    return (name in REQUIRED or any(name.startswith(prefix + "/") for prefix in DIRECTORIES)) and (
        len(parts) >= 1
    )


def _regular(path: Path) -> None:
    if path.is_symlink() or not path.is_file():
        raise SnapshotError(f"备份源包含符号链接或非普通文件：{path}")


def _refuse_symlink_parents(root: Path, relative: str) -> None:
    """Reject indirection at every level, not just the last path component."""
    current = root
    for part in PurePosixPath(relative).parts:
        current = current / part
        if current.is_symlink():
            raise SnapshotError(f"备份源路径不可穿越符号链接：{current}")


def _collect(root: Path) -> list[str]:
    for required in REQUIRED:
        _refuse_symlink_parents(root, required)
        _regular(root / required)
    paths = set(REQUIRED)
    for directory in DIRECTORIES:
        _refuse_symlink_parents(root, directory)
        source = root / directory
        if source.is_symlink():
            raise SnapshotError(f"备份目录不能是符号链接：{source}")
        if not source.exists():
            continue
        if not source.is_dir():
            raise SnapshotError(f"备份目录无效：{source}")
        for parent, dirs, files in os.walk(source, followlinks=False):
            for name in dirs:
                if (Path(parent) / name).is_symlink():
                    raise SnapshotError("备份目录中存在符号链接")
            for name in files:
                full = Path(parent) / name
                relative = full.relative_to(root).as_posix()
                if relative in SKIP or any(
                    relative == db + extension for db in DATABASES
                    for extension in ("-wal", "-shm", "-journal")
                ):
                    continue
                _regular(full)
                if not _valid_member(relative):
                    raise SnapshotError(f"备份文件路径不合法：{relative}")
                paths.add(relative)
    return sorted(paths)


def _backup_sqlite(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as reader:
        with closing(sqlite3.connect(destination)) as writer, writer:
            reader.backup(writer)
    if not _sqlite_ok(destination):
        raise SnapshotError(f"SQLite 备份完整性检查失败：{source}")


def _sqlite_ok(path: Path) -> bool:
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            return db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    except sqlite3.Error:
        return False


def _digest(path: Path) -> dict[str, object]:
    hash_ = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            hash_.update(chunk)
    return {"sha256": hash_.hexdigest(), "size": path.stat().st_size}


def create_snapshot(root: Path, output: Path, *, offline_confirmed: bool = False) -> dict:
    """Create an atomic, non-overwriting 0600 archive from a stopped service."""
    root, output = Path(root).resolve(), Path(output).absolute()
    if not offline_confirmed:
        raise SnapshotError("须先停止所有写入进程并显式指定 --offline-confirmed")
    if any(output.is_relative_to(root / directory) for directory in DIRECTORIES):
        raise SnapshotError("备份目标不能放在受备份的数据目录内，避免下次快照递归收录")
    if service_is_active():
        raise SnapshotError("rm-progress 服务仍在运行：必须停止服务后再生成一致性快照")
    if output.exists() or output.is_symlink():
        raise SnapshotError("备份目标已存在；禁止覆盖原有恢复点")
    if not output.parent.is_dir():
        raise SnapshotError("备份输出目录不存在")
    names = _collect(root)
    with tempfile.TemporaryDirectory(prefix=".snapshot-stage-") as stage_name:
        stage = Path(stage_name)
        manifest = {
            "format_version": VERSION,
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "consistency": "offline-writers-stopped-operator-confirmed",
            "files": {},
        }
        for name in names:
            source, destination = root / name, stage / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            if name in DATABASES:
                _backup_sqlite(source, destination)
            else:
                shutil.copyfile(source, destination, follow_symlinks=False)
            manifest["files"][name] = _digest(destination)

        fd, tmp_file = tempfile.mkstemp(prefix=".snapshot-", suffix=".tmp", dir=output.parent)
        os.chmod(tmp_file, 0o600)
        try:
            with os.fdopen(fd, "wb") as stream:
                with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED,
                                     allowZip64=True) as archive:
                    for name in names:
                        archive.write(stage / name, arcname=name)
                    archive.writestr(MANIFEST, json.dumps(
                        manifest, ensure_ascii=False, sort_keys=True, indent=2,
                    ).encode("utf-8"))
                stream.flush()
                os.fsync(stream.fileno())
            verify_snapshot(Path(tmp_file))
            # Atomic link fails rather than replacing an existing recovery point.
            os.link(tmp_file, output)
        finally:
            if os.path.exists(tmp_file):
                os.unlink(tmp_file)
    return manifest


def verify_snapshot(archive_path: Path) -> dict:
    """Verify every file SHA/length, manifest membership and SQLite pages."""
    try:
        with zipfile.ZipFile(archive_path) as archive:
            members = archive.infolist()
            names = [member.filename for member in members]
            if len(set(names)) != len(names) or MANIFEST not in names:
                raise SnapshotError("备份条目重复或缺少清单")
            manifest = json.loads(archive.read(MANIFEST))
            if manifest.get("format_version") != VERSION or not isinstance(manifest.get("files"), dict):
                raise SnapshotError("快照清单版本或结构不合法")
            records = manifest["files"]
            if not set(REQUIRED).issubset(records):
                raise SnapshotError("快照缺少必需的数据库或会话密钥")
            if set(names) != set(records) | {MANIFEST}:
                raise SnapshotError("压缩包与清单条目不一致")
            for name, expected in records.items():
                if not _valid_member(name) or name in SKIP or not isinstance(expected, dict):
                    raise SnapshotError(f"非法备份路径：{name}")
                info = archive.getinfo(name)
                if info.is_dir() or stat.S_ISLNK(info.external_attr >> 16):
                    raise SnapshotError("备份包含非普通文件")
                digest = hashlib.sha256()
                size = 0
                with archive.open(name) as source:
                    for block in iter(lambda: source.read(1024 * 1024), b""):
                        digest.update(block)
                        size += len(block)
                if digest.hexdigest() != expected.get("sha256") or size != expected.get("size"):
                    raise SnapshotError(f"备份校验失败：{name}")
            with tempfile.TemporaryDirectory(prefix="xtyf-verify-db-") as tmp:
                for name in DATABASES:
                    path = Path(tmp) / Path(name).name
                    with archive.open(name) as source, path.open("wb") as destination:
                        shutil.copyfileobj(source, destination)
                    if not _sqlite_ok(path):
                        raise SnapshotError(f"备份 SQLite 完整性检查失败：{name}")
            return manifest
    except (OSError, zipfile.BadZipFile, KeyError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise SnapshotError(f"备份不可验证：{exc}") from exc


def restore_snapshot(archive_path: Path, target: Path) -> dict:
    """Restore only into a NEW directory; never overwrite an existing deployment."""
    target = Path(target).absolute()
    if target.exists() or target.is_symlink():
        raise SnapshotError("恢复目录已存在；只允许恢复到全新的隔离目录")
    if not target.parent.is_dir():
        raise SnapshotError("恢复目录的父目录不存在")
    manifest = verify_snapshot(archive_path)
    temp = Path(tempfile.mkdtemp(prefix=".xtyf-restore-", dir=target.parent))
    try:
        os.chmod(temp, 0o700)
        with zipfile.ZipFile(archive_path) as archive:
            for name in sorted(manifest["files"]):
                output = temp.joinpath(*PurePosixPath(name).parts)
                output.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(name) as source, output.open("xb") as destination:
                    shutil.copyfileobj(source, destination)
                os.chmod(output, 0o600)
        for directory in DIRECTORIES:
            (temp / directory).mkdir(parents=True, exist_ok=True)
        temp.rename(target)
    except BaseException:
        shutil.rmtree(temp)
        raise
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline verified full-data snapshot/restore")
    command = parser.add_subparsers(dest="command", required=True)
    create = command.add_parser("create")
    create.add_argument("--app-dir", type=Path, required=True)
    create.add_argument("--output", type=Path, required=True)
    create.add_argument("--offline-confirmed", action="store_true")
    verify = command.add_parser("verify")
    verify.add_argument("--archive", type=Path, required=True)
    restore = command.add_parser("restore")
    restore.add_argument("--archive", type=Path, required=True)
    restore.add_argument("--target", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "create":
            result = create_snapshot(args.app_dir, args.output,
                                     offline_confirmed=args.offline_confirmed)
        elif args.command == "verify":
            result = verify_snapshot(args.archive)
        else:
            result = restore_snapshot(args.archive, args.target)
        print(f"快照 {args.command} 校验成功：{len(result['files'])} 个文件")
    except SnapshotError as exc:
        parser.exit(1, f"快照操作拒绝：{exc}\n")


if __name__ == "__main__":
    main()
