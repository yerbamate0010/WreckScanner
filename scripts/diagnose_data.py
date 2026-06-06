#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from core import config  # noqa: E402
from core.data_diagnostics import format_data_diagnostics, run_data_diagnostics  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sprawdza spójność lokalnych danych WreckScanner.")
    parser.add_argument("--field-photos-dir", type=Path, default=config.FIELD_PHOTOS_DIR)
    parser.add_argument("--wrecks-dir", type=Path, default=config.WRECKS_DIR)
    parser.add_argument("--private-photos-dir", type=Path, default=config.PRIVATE_PHOTOS_DIR)
    parser.add_argument("--json", action="store_true", help="Wypisz pełny raport JSON zamiast podsumowania tekstowego.")
    parser.add_argument("--output-json", type=Path, help="Zapisz pełny raport JSON do pliku.")
    parser.add_argument(
        "--no-image-check", action="store_true", help="Nie otwieraj obrazów; sprawdź tylko rekordy i ścieżki."
    )
    parser.add_argument("--strict", action="store_true", help="Zwróć kod błędu także przy ostrzeżeniach.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = run_data_diagnostics(
        field_photos_dir=args.field_photos_dir,
        wrecks_dir=args.wrecks_dir,
        private_photos_dir=args.private_photos_dir,
        check_images=not args.no_image_check,
    )

    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(format_data_diagnostics(report))

    issue_counts = report["summary"]["issues"]["by_severity"]
    if issue_counts["error"] > 0:
        return 1
    if args.strict and (issue_counts["warning"] > 0 or issue_counts["info"] > 0):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
