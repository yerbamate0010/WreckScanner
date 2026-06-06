from __future__ import annotations

from dataclasses import dataclass
from email import policy
from email.parser import BytesParser


@dataclass(frozen=True)
class UploadedFile:
    field_name: str
    filename: str
    content_type: str
    data: bytes


def parse_multipart_form(
    content_type: str,
    body: bytes,
    *,
    max_body_bytes: int | None = None,
) -> tuple[dict[str, str], list[UploadedFile]]:
    if not content_type.lower().startswith("multipart/form-data"):
        raise ValueError("Payload musi być multipart/form-data.")
    if not body:
        raise ValueError("Brak danych formularza.")
    if max_body_bytes is not None and len(body) > max_body_bytes:
        raise ValueError("Formularz przekracza limit rozmiaru pakietu.")

    header = f"Content-Type: {content_type}\r\n\r\n".encode("utf-8")
    message = BytesParser(policy=policy.default).parsebytes(header + body)
    if not message.is_multipart():
        raise ValueError("Nieprawidłowy formularz multipart.")

    fields: dict[str, str] = {}
    files: list[UploadedFile] = []
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if not name:
            continue
        payload = part.get_payload(decode=True) or b""
        filename = part.get_filename()
        if filename is None:
            charset = part.get_content_charset() or "utf-8"
            fields[str(name)] = payload.decode(charset, errors="replace")
            continue
        files.append(
            UploadedFile(
                field_name=str(name),
                filename=str(filename),
                content_type=part.get_content_type(),
                data=payload,
            )
        )
    return fields, files
