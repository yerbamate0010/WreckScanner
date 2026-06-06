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
from core.photo_retention import retire_private_originals  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Zastępuje albo usuwa prywatne oryginały zdjęć po terminie retencji.")
    parser.add_argument("--field-photos-dir", type=Path, default=config.FIELD_PHOTOS_DIR)
    parser.add_argument("--wrecks-dir", type=Path, default=config.WRECKS_DIR)
    parser.add_argument("--private-photos-dir", type=Path, default=config.PRIVATE_PHOTOS_DIR)
    parser.add_argument("--retention-days", type=int, default=config.PRIVATE_ORIGINAL_RETENTION_DAYS)
    parser.add_argument("--apply", action="store_true", help="Zapisz zmiany. Bez tej flagi działa tylko dry-run.")
    parser.add_argument("--json", action="store_true", help="Wypisz pełny raport JSON.")
    return parser.parse_args()


def _format(report: dict) -> str:
    field = report["field_photos"]
    wreck = report["wreck_photos"]
    lines = [
        "Retencja prywatnych oryginałów zdjęć",
        f"Tryb: {'apply' if not report['dry_run'] else 'dry-run'}",
        f"Limit: {report['retention_days']} dni od ostatniej weryfikacji",
        (
            "Zdjęcia terenowe: "
            f"sprawdzone={field['scanned']}, zastąpione={field['replaced']}, "
            f"usunięte={field['deleted']}, pominięte={field['skipped']}"
        ),
        (
            "Zdjęcia w sprawach: "
            f"sprawdzone={wreck['scanned']}, zastąpione={wreck['replaced']}, "
            f"usunięte={wreck['deleted']}, pominięte={wreck['skipped']}"
        ),
    ]
    if report["items"]:
        lines.append("Zmiany:")
        for item in report["items"]:
            label = item.get("id") or "-"
            if item.get("scope") == "wreck":
                label = f"{item.get('wreck_id')}/{label}"
            lines.append(f"  - {item.get('scope')}: {label}: {item.get('action')}")
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    report = retire_private_originals(
        field_photos_dir=args.field_photos_dir,
        wrecks_dir=args.wrecks_dir,
        private_photos_dir=args.private_photos_dir,
        retention_days=args.retention_days,
        dry_run=not args.apply,
    )
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(_format(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
