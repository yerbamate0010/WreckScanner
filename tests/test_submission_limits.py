import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from core.submission_limits import pending_submission_usage


def write_json(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


class PendingSubmissionLimitTests(unittest.TestCase):
    def test_counts_only_pending_items_for_owner(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            private_dir = root / "private"
            field_dir = root / "field"
            wrecks_dir = root / "wrecks"
            (private_dir / "field_photos/pending/original.jpg").parent.mkdir(parents=True)
            (private_dir / "field_photos/pending/original.jpg").write_bytes(b"a" * 10)
            (private_dir / "field_photos/rejected/original.jpg").parent.mkdir(parents=True)
            (private_dir / "field_photos/rejected/original.jpg").write_bytes(b"b" * 20)
            write_json(
                field_dir / "pending" / "record.json",
                {
                    "id": "pending",
                    "public_review_status": "pending",
                    "submission_owner": "public:a",
                    "private_original_file": "field_photos/pending/original.jpg",
                },
            )
            write_json(
                field_dir / "rejected" / "record.json",
                {
                    "id": "rejected",
                    "public_review_status": "rejected",
                    "submission_owner": "public:a",
                    "private_original_file": "field_photos/rejected/original.jpg",
                },
            )

            usage = pending_submission_usage(
                owner="public:a",
                wrecks_dir=wrecks_dir,
                field_photos_dir=field_dir,
                private_dir=private_dir,
            )

            self.assertEqual(usage["items"], 1)
            self.assertEqual(usage["bytes"], 10)


if __name__ == "__main__":
    unittest.main()
