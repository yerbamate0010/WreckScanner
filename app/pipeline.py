from __future__ import annotations

import os
import secrets
import threading
import time

from app import config


class HttpJsonError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


_pipeline_lock = threading.Lock()
_pipeline_state = {
    "status": "idle",
    "token": None,
    "client": None,
    "stage": None,
    "started_at": None,
    "updated_at": None,
}


def load_average() -> tuple[float | None, float | None]:
    try:
        load_1m = os.getloadavg()[0]
    except (AttributeError, OSError):
        return None, None
    cpus = os.cpu_count() or 1
    return load_1m, load_1m / cpus


def available_memory_mb() -> float | None:
    try:
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) / 1024
    except OSError:
        return None
    return None


def system_pressure() -> dict:
    load_1m, load_per_cpu = load_average()
    mem_available_mb = available_memory_mb()
    overloaded = False
    reasons = []
    if load_per_cpu is not None and load_per_cpu > config.MAX_LOAD_PER_CPU:
        overloaded = True
        reasons.append(f"load {load_1m:.2f} jest za wysoki")
    if mem_available_mb is not None and mem_available_mb < config.MIN_AVAILABLE_MEMORY_MB:
        overloaded = True
        reasons.append(f"wolna pamiec {mem_available_mb:.0f} MB jest za niska")
    return {
        "overloaded": overloaded,
        "reasons": reasons,
        "load_1m": round(load_1m, 2) if load_1m is not None else None,
        "load_per_cpu": round(load_per_cpu, 2) if load_per_cpu is not None else None,
        "memory_available_mb": round(mem_available_mb) if mem_available_mb is not None else None,
        "limits": {
            "max_load_per_cpu": config.MAX_LOAD_PER_CPU,
            "min_available_memory_mb": config.MIN_AVAILABLE_MEMORY_MB,
        },
    }


def client_id(handler) -> str:
    cf_ip = handler.headers.get("CF-Connecting-IP") if hasattr(handler, "headers") else None
    forwarded = handler.headers.get("X-Forwarded-For") if hasattr(handler, "headers") else None
    if cf_ip:
        return cf_ip.strip()
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    return getattr(handler, "client_address", ("unknown",))[0]


def cleanup_stale_pipeline(now: float | None = None) -> None:
    now = time.time() if now is None else now
    if _pipeline_state["status"] == "idle":
        return
    updated_at = _pipeline_state.get("updated_at") or _pipeline_state.get("started_at") or 0
    if now - float(updated_at) <= config.PIPELINE_TTL_SECONDS:
        return
    _pipeline_state.update(
        {
            "status": "idle",
            "token": None,
            "client": None,
            "stage": None,
            "started_at": None,
            "updated_at": now,
        }
    )


def pipeline_snapshot() -> dict:
    with _pipeline_lock:
        cleanup_stale_pipeline()
        return {
            "status": _pipeline_state["status"],
            "stage": _pipeline_state["stage"],
            "started_at": _pipeline_state["started_at"],
            "updated_at": _pipeline_state["updated_at"],
        }


def start_pipeline(client: str) -> str:
    with _pipeline_lock:
        now = time.time()
        cleanup_stale_pipeline(now)
        if _pipeline_state["status"] != "idle":
            raise HttpJsonError(429, "Serwer jest zajety aktualna analiza. Sprobuj ponownie za chwile.")
        token = secrets.token_urlsafe(24)
        _pipeline_state.update(
            {
                "status": "active",
                "token": token,
                "client": client,
                "stage": "download",
                "started_at": now,
                "updated_at": now,
            }
        )
        return token


def advance_pipeline(token: str, client: str, stage: str) -> None:
    with _pipeline_lock:
        now = time.time()
        cleanup_stale_pipeline(now)
        if _pipeline_state["status"] != "active":
            raise HttpJsonError(409, "Brak aktywnego pobierania dla tej analizy. Uruchom skan od poczatku.")
        if _pipeline_state["token"] != token or _pipeline_state["client"] != client:
            raise HttpJsonError(429, "Inny uzytkownik korzysta teraz ze slotu analizy. Sprobuj ponownie pozniej.")
        _pipeline_state["stage"] = stage
        _pipeline_state["updated_at"] = now


def finish_pipeline(token: str | None = None) -> None:
    with _pipeline_lock:
        if token is not None and _pipeline_state["token"] != token:
            return
        _pipeline_state.update(
            {
                "status": "idle",
                "token": None,
                "client": None,
                "stage": None,
                "started_at": None,
                "updated_at": time.time(),
            }
        )


def progress_percent(current: int | float, total: int | float) -> float | None:
    if not total:
        return None
    return max(0.0, min(100.0, float(current) / float(total) * 100.0))
