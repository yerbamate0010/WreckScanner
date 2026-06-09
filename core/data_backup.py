from __future__ import annotations

import json
import os
import subprocess  # nosec B404
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core import config
from core.data_diagnostics import run_data_diagnostics

DEFAULT_DIAGNOSTICS_OUTPUT = config.OUTPUT_DIR / "data_diagnostics.json"
DEFAULT_RESTIC_TAGS = ("wreckscanner", "data")

Runner = Callable[..., subprocess.CompletedProcess[Any]]


@dataclass(frozen=True)
class ResticOptions:
    root_dir: Path
    restic_bin: str = "restic"
    repository: str | None = None
    password_file: Path | None = None


@dataclass(frozen=True)
class ResticCommandResult:
    command: list[str]
    returncode: int
    error: str | None = None


@dataclass(frozen=True)
class BackupRunResult:
    status: str
    diagnostics_status: str
    diagnostics_report: dict[str, Any]
    diagnostics_output: Path
    backup_paths: list[Path]
    message: str
    restic: ResticCommandResult | None = None


def _json_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _resolve(root_dir: Path, path: Path) -> Path:
    path = path.expanduser()
    return path if path.is_absolute() else root_dir / path


def _path_arg(root_dir: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root_dir.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _dedupe(paths: list[Path]) -> list[Path]:
    seen: set[Path] = set()
    unique: list[Path] = []
    for path in paths:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(path)
    return unique


def _restic_env(options: ResticOptions) -> dict[str, str]:
    env = os.environ.copy()
    if options.repository:
        env["RESTIC_REPOSITORY"] = options.repository
    if options.password_file:
        env["RESTIC_PASSWORD_FILE"] = str(_resolve(options.root_dir, options.password_file))
    env.setdefault("RESTIC_CACHE_DIR", str(options.root_dir / ".cache" / "restic"))
    if not env.get("RESTIC_REPOSITORY"):
        raise ValueError("Podaj --repo albo ustaw RESTIC_REPOSITORY.")
    if not env.get("RESTIC_PASSWORD") and not env.get("RESTIC_PASSWORD_FILE"):
        raise ValueError("Podaj --password-file albo ustaw RESTIC_PASSWORD_FILE/RESTIC_PASSWORD.")
    return env


def run_restic_command(
    args: list[str],
    options: ResticOptions,
    *,
    runner: Runner = subprocess.run,
) -> ResticCommandResult:
    command = [options.restic_bin, *args]
    try:
        env = _restic_env(options)
        completed = runner(command, cwd=options.root_dir, env=env, check=False)
    except FileNotFoundError as exc:
        return ResticCommandResult(command=command, returncode=127, error=str(exc))
    except ValueError as exc:
        return ResticCommandResult(command=command, returncode=2, error=str(exc))
    return ResticCommandResult(command=command, returncode=int(completed.returncode))


def collect_backup_paths(
    *,
    root_dir: Path,
    diagnostics_output: Path,
    include_admin_password: bool = False,
    extra_paths: list[Path] | None = None,
) -> tuple[list[Path], list[Path]]:
    candidates = [
        _resolve(root_dir, config.WRECKS_DIR),
        _resolve(root_dir, config.FIELD_PHOTOS_DIR),
        _resolve(root_dir, config.PRIVATE_PHOTOS_DIR),
        _resolve(root_dir, config.PRIVATE_REPORTS_DIR),
        _resolve(root_dir, config.PRIVACY_REQUESTS_DIR),
        _resolve(root_dir, Path(config.SETTINGS_FILENAME)),
        _resolve(root_dir, diagnostics_output),
    ]
    if include_admin_password:
        candidates.append(root_dir / ".admin_password")
    missing: list[Path] = []
    for extra_path in extra_paths or []:
        resolved = _resolve(root_dir, extra_path)
        if not resolved.exists():
            missing.append(resolved)
        candidates.append(resolved)

    existing = [path for path in candidates if path.exists()]
    return _dedupe(existing), missing


def run_backup(
    *,
    options: ResticOptions,
    diagnostics_output: Path = DEFAULT_DIAGNOSTICS_OUTPUT,
    include_admin_password: bool = False,
    extra_paths: list[Path] | None = None,
    strict: bool = False,
    check_images: bool = True,
    dry_run: bool = False,
    tags: tuple[str, ...] = DEFAULT_RESTIC_TAGS,
    runner: Runner = subprocess.run,
) -> BackupRunResult:
    root_dir = options.root_dir
    diagnostics_path = _resolve(root_dir, diagnostics_output)
    report = run_data_diagnostics(
        field_photos_dir=_resolve(root_dir, config.FIELD_PHOTOS_DIR),
        wrecks_dir=_resolve(root_dir, config.WRECKS_DIR),
        check_images=check_images,
    )
    _json_write(diagnostics_path, report)

    issue_counts = report["summary"]["issues"]["by_severity"]
    if issue_counts["error"] > 0:
        return BackupRunResult(
            status="blocked",
            diagnostics_status=str(report["status"]),
            diagnostics_report=report,
            diagnostics_output=diagnostics_path,
            backup_paths=[],
            message="Backup przerwany: diagnostyka danych ma błędy.",
        )
    if strict and (issue_counts["warning"] > 0 or issue_counts["info"] > 0):
        return BackupRunResult(
            status="blocked",
            diagnostics_status=str(report["status"]),
            diagnostics_report=report,
            diagnostics_output=diagnostics_path,
            backup_paths=[],
            message="Backup przerwany: tryb strict blokuje ostrzeżenia diagnostyki.",
        )

    backup_paths, missing_extra_paths = collect_backup_paths(
        root_dir=root_dir,
        diagnostics_output=diagnostics_output,
        include_admin_password=include_admin_password,
        extra_paths=extra_paths,
    )
    if missing_extra_paths:
        return BackupRunResult(
            status="blocked",
            diagnostics_status=str(report["status"]),
            diagnostics_report=report,
            diagnostics_output=diagnostics_path,
            backup_paths=backup_paths,
            message="Backup przerwany: dodatkowa ścieżka nie istnieje.",
        )
    if not backup_paths:
        return BackupRunResult(
            status="blocked",
            diagnostics_status=str(report["status"]),
            diagnostics_report=report,
            diagnostics_output=diagnostics_path,
            backup_paths=[],
            message="Backup przerwany: brak istniejących ścieżek do backupu.",
        )

    args = ["backup"]
    if dry_run:
        args.append("--dry-run")
    for tag in tags:
        args.extend(["--tag", tag])
    args.extend(_path_arg(root_dir, path) for path in backup_paths)

    restic_result = run_restic_command(args, options, runner=runner)
    status = "ok" if restic_result.returncode == 0 else "failed"
    message = "Backup zakończony." if status == "ok" else "Backup nie powiódł się."
    if restic_result.error:
        message = restic_result.error
    return BackupRunResult(
        status=status,
        diagnostics_status=str(report["status"]),
        diagnostics_report=report,
        diagnostics_output=diagnostics_path,
        backup_paths=backup_paths,
        message=message,
        restic=restic_result,
    )


def restic_init(options: ResticOptions, *, runner: Runner = subprocess.run) -> ResticCommandResult:
    return run_restic_command(["init"], options, runner=runner)


def restic_check(options: ResticOptions, *, runner: Runner = subprocess.run) -> ResticCommandResult:
    return run_restic_command(["check"], options, runner=runner)


def restic_snapshots(options: ResticOptions, *, runner: Runner = subprocess.run) -> ResticCommandResult:
    return run_restic_command(["snapshots"], options, runner=runner)


def restic_forget(
    options: ResticOptions,
    *,
    keep_daily: int,
    keep_weekly: int,
    keep_monthly: int,
    prune: bool,
    runner: Runner = subprocess.run,
) -> ResticCommandResult:
    args = [
        "forget",
        "--keep-daily",
        str(keep_daily),
        "--keep-weekly",
        str(keep_weekly),
        "--keep-monthly",
        str(keep_monthly),
    ]
    if prune:
        args.append("--prune")
    return run_restic_command(args, options, runner=runner)
