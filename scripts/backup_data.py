#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from core.data_backup import (  # noqa: E402
    DEFAULT_DIAGNOSTICS_OUTPUT,
    ResticCommandResult,
    ResticOptions,
    restic_check,
    restic_forget,
    restic_init,
    restic_snapshots,
    run_backup,
)
from core.data_diagnostics import format_data_diagnostics  # noqa: E402


def _common_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--repo", help="Repozytorium restic. Alternatywnie ustaw RESTIC_REPOSITORY.")
    parser.add_argument(
        "--password-file", type=Path, help="Plik hasła restic. Alternatywnie ustaw RESTIC_PASSWORD_FILE."
    )
    parser.add_argument("--restic-bin", default="restic", help="Ścieżka do binarki restic.")
    parser.add_argument("--root-dir", type=Path, default=ROOT_DIR, help="Katalog projektu WreckScanner.")
    return parser


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backup lokalnej bazy WreckScanner przez restic.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    common = _common_parser()

    subparsers.add_parser("init", parents=[common], help="Zainicjuj repozytorium restic.")
    subparsers.add_parser("check", parents=[common], help="Sprawdź repozytorium restic.")
    subparsers.add_parser("snapshots", parents=[common], help="Pokaż snapshoty restic.")

    run_parser = subparsers.add_parser("run", parents=[common], help="Wykonaj diagnostykę i backup danych.")
    run_parser.add_argument("--diagnostics-output", type=Path, default=DEFAULT_DIAGNOSTICS_OUTPUT)
    run_parser.add_argument(
        "--include-admin-password", action="store_true", help="Dołącz lokalny plik .admin_password."
    )
    run_parser.add_argument(
        "--include-path", type=Path, action="append", default=[], help="Dodatkowa ścieżka do backupu."
    )
    run_parser.add_argument("--no-image-check", action="store_true", help="Nie otwieraj obrazów podczas diagnostyki.")
    run_parser.add_argument(
        "--strict", action="store_true", help="Przerwij backup także przy ostrzeżeniach diagnostyki."
    )
    run_parser.add_argument("--dry-run", action="store_true", help="Przekaż --dry-run do restic backup.")

    forget_parser = subparsers.add_parser("forget", parents=[common], help="Zastosuj retencję snapshotów.")
    forget_parser.add_argument("--keep-daily", type=int, default=14)
    forget_parser.add_argument("--keep-weekly", type=int, default=8)
    forget_parser.add_argument("--keep-monthly", type=int, default=6)
    forget_parser.add_argument("--prune", action="store_true", help="Po retencji zwolnij nieużywane dane repozytorium.")

    return parser.parse_args()


def _options(args: argparse.Namespace) -> ResticOptions:
    return ResticOptions(
        root_dir=args.root_dir.resolve(),
        restic_bin=args.restic_bin,
        repository=args.repo,
        password_file=args.password_file,
    )


def _print_restic_result(result: ResticCommandResult) -> int:
    if result.error:
        print(result.error, file=sys.stderr)
    return result.returncode


def _run_backup(args: argparse.Namespace) -> int:
    options = _options(args)
    result = run_backup(
        options=options,
        diagnostics_output=args.diagnostics_output,
        include_admin_password=args.include_admin_password,
        extra_paths=args.include_path,
        strict=args.strict,
        check_images=not args.no_image_check,
        dry_run=args.dry_run,
    )

    print(format_data_diagnostics(result.diagnostics_report))
    print("")
    print(f"Diagnostyka zapisana: {result.diagnostics_output}")
    if result.backup_paths:
        print("Ścieżki backupu:")
        for path in result.backup_paths:
            print(f"- {path}")
    print(result.message)
    if result.restic:
        print("Polecenie restic:")
        print(" ".join(result.restic.command))
        return result.restic.returncode
    return 0 if result.status == "ok" else 1


def main() -> int:
    args = parse_args()
    options = _options(args)
    if args.command == "init":
        return _print_restic_result(restic_init(options))
    if args.command == "check":
        return _print_restic_result(restic_check(options))
    if args.command == "snapshots":
        return _print_restic_result(restic_snapshots(options))
    if args.command == "forget":
        return _print_restic_result(
            restic_forget(
                options,
                keep_daily=args.keep_daily,
                keep_weekly=args.keep_weekly,
                keep_monthly=args.keep_monthly,
                prune=args.prune,
            )
        )
    if args.command == "run":
        return _run_backup(args)
    raise AssertionError(f"Nieznana komenda: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
