from __future__ import annotations

import os
import sys

TEXT_ENCODING = "utf-8"
TEXT_ERRORS = "replace"
PYTHON_IO_ENCODING = f"{TEXT_ENCODING}:{TEXT_ERRORS}"


def configure_process_encoding() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding=TEXT_ENCODING, errors=TEXT_ERRORS)
        except (TypeError, ValueError):
            pass
    os.environ["PYTHONIOENCODING"] = PYTHON_IO_ENCODING


def subprocess_text_kwargs() -> dict[str, str]:
    return {"encoding": TEXT_ENCODING, "errors": TEXT_ERRORS}
