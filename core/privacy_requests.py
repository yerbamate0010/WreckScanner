from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core import config
from core.json_io import write_json_atomic

PRIVACY_REQUEST_STATUSES = {"new", "in_progress", "done", "rejected"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _safe_text(value: Any, max_len: int) -> str:
    text = str(value or "").replace("\x00", "").strip()
    if len(text) > max_len:
        raise ValueError("Jedno z pól formularza jest zbyt długie.")
    return text


def _request_id(created_at: str, email: str) -> str:
    digest = hashlib.sha1(
        f"{created_at}:{email}:{secrets.token_urlsafe(12)}".encode(),
        usedforsecurity=False,
    ).hexdigest()[:10]
    stamp = created_at.replace("-", "").replace(":", "").removesuffix("Z")
    return f"privacy_{stamp}_{digest}"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    write_json_atomic(path, payload)


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Nieprawidłowy format zgłoszenia.")
    return payload


def _safe_request_id(value: Any) -> str:
    request_id = str(value or "").strip()
    if not request_id.startswith("privacy_") or "/" in request_id or "\\" in request_id or ".." in request_id:
        raise ValueError("Nieprawidłowy identyfikator zgłoszenia.")
    return request_id


def _normalize_status(value: Any) -> str:
    status = str(value or "new").strip()
    if status not in PRIVACY_REQUEST_STATUSES:
        raise ValueError("Nieprawidłowy status zgłoszenia.")
    return status


def _request_path(request_id: str, storage_dir: Path) -> Path:
    request_id = _safe_request_id(request_id)
    root = storage_dir.resolve()
    path = (storage_dir / f"{request_id}.json").resolve()
    if root != path and root not in path.parents:
        raise ValueError("Nieprawidłowa ścieżka zgłoszenia.")
    return path


def _ensure_request_fields(payload: dict[str, Any]) -> bool:
    changed = False
    if "status" not in payload:
        payload["status"] = "new"
        changed = True
    else:
        payload["status"] = _normalize_status(payload.get("status"))
    if "updated_at" not in payload:
        payload["updated_at"] = payload.get("created_at")
        changed = True
    if "handled_at" not in payload:
        payload["handled_at"] = payload.get("updated_at") if payload.get("status") in {"done", "rejected"} else None
        changed = True
    if "admin_note" not in payload:
        payload["admin_note"] = ""
        changed = True
    return changed


def create_privacy_request(fields: dict[str, Any], storage_dir: Path | None = None) -> dict[str, Any]:
    storage = storage_dir or config.PRIVACY_REQUESTS_DIR
    email = _safe_text(fields.get("email"), 180)
    target = _safe_text(fields.get("target"), 500)
    reason = _safe_text(fields.get("reason"), 4000)
    if not email or not target or not reason:
        raise ValueError("Uzupełnij e-mail, link albo identyfikator wpisu oraz opis żądania.")
    created_at = _now_iso()
    request_id = _request_id(created_at, email)
    payload = {
        "id": request_id,
        "created_at": created_at,
        "status": "new",
        "email": email,
        "target": target,
        "reason": reason,
        "updated_at": created_at,
        "handled_at": None,
        "admin_note": "",
    }
    _write_json(storage / f"{request_id}.json", payload)
    return {"status": "ok", "request_id": request_id}


def list_privacy_requests(storage_dir: Path | None = None, *, status: Any = "all") -> list[dict[str, Any]]:
    storage = storage_dir or config.PRIVACY_REQUESTS_DIR
    if not storage.is_dir():
        return []
    status_filter = str(status or "all").strip()
    if status_filter != "all" and status_filter not in PRIVACY_REQUEST_STATUSES:
        raise ValueError("Nieprawidłowy filtr statusu zgłoszeń.")
    requests: list[dict[str, Any]] = []
    for path in sorted(storage.glob("privacy_*.json")):
        try:
            payload = _read_json(path)
            if _ensure_request_fields(payload):
                _write_json(path, payload)
        except (OSError, json.JSONDecodeError, ValueError):
            continue
        if payload.get("id") and (status_filter == "all" or payload.get("status") == status_filter):
            requests.append(payload)
    return sorted(requests, key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""), reverse=True)


def update_privacy_request(request_id: str, fields: dict[str, Any], storage_dir: Path | None = None) -> dict[str, Any]:
    storage = storage_dir or config.PRIVACY_REQUESTS_DIR
    path = _request_path(request_id, storage)
    if not path.exists():
        raise FileNotFoundError("Nie znaleziono zgłoszenia.")
    payload = _read_json(path)
    if str(payload.get("id") or "") != _safe_request_id(request_id):
        raise ValueError("ID zgłoszenia nie zgadza się z nazwą pliku.")
    _ensure_request_fields(payload)
    status = _normalize_status(fields.get("status", payload.get("status")))
    admin_note = _safe_text(fields.get("admin_note", payload.get("admin_note")), 4000)
    updated_at = _now_iso()
    payload["status"] = status
    payload["admin_note"] = admin_note
    payload["updated_at"] = updated_at
    payload["handled_at"] = updated_at if status in {"done", "rejected"} else None
    _write_json(path, payload)
    return {"status": "ok", "request": payload}
