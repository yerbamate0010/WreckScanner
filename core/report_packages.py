from __future__ import annotations

import hashlib
import html
import io
import json
import re
import secrets
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError

from core import config
from core.photo_privacy import is_approved
from core.report_pdf import PdfPhoto, build_report_pdf
from core.wrecks import render_wreck_record_html

REQUIRED_REPORT_FIELDS = {
    "reporter_name": "imię i nazwisko",
    "reporter_address": "adres zamieszkania",
    "reporter_phone": "telefon",
    "reporter_email": "adres e-mail",
    "location_description": "dokładne miejsce pojazdu",
    "observed_at": "data i godzina obserwacji",
    "vehicle_description": "opis stanu pojazdu",
}


@dataclass(frozen=True)
class ReportPhotoUpload:
    field_name: str
    filename: str
    content_type: str
    data: bytes


@dataclass(frozen=True)
class PreparedReportPhoto:
    original_name: str
    optimized_name: str
    original_data: bytes
    optimized_data: bytes
    content_type: str
    size_bytes: int
    optimized_size_bytes: int


@dataclass(frozen=True)
class ReportPackageAccess:
    token: str
    expires_at: str


def _now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _validate_wreck_id(wreck_id: str) -> str:
    if not re.fullmatch(r"wreck_-?\d+_-?\d+", wreck_id):
        raise ValueError("Nieprawidłowy identyfikator teczki pojazdu.")
    return wreck_id


def _safe_text(value: Any, max_len: int = 4000) -> str:
    text = str(value or "").replace("\x00", "").strip()
    if len(text) > max_len:
        raise ValueError("Jedno z pól formularza jest zbyt długie.")
    return text


def _validate_fields(raw_fields: dict[str, str]) -> dict[str, str]:
    fields = {key: _safe_text(raw_fields.get(key)) for key in REQUIRED_REPORT_FIELDS}
    missing = [label for key, label in REQUIRED_REPORT_FIELDS.items() if not fields[key]]
    if missing:
        raise ValueError("Uzupełnij wymagane pola: " + ", ".join(missing) + ".")
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", fields["reporter_email"]):
        raise ValueError("Podaj prawidłowy adres e-mail zgłaszającego.")
    return fields


def _record_dir_for(wreck_id: str, wrecks_dir: Path) -> Path:
    wreck_id = _validate_wreck_id(wreck_id)
    root = wrecks_dir.resolve()
    record_dir = (wrecks_dir / wreck_id).resolve()
    if root != record_dir and root not in record_dir.parents:
        raise ValueError("Nieprawidłowa ścieżka teczki pojazdu.")
    if not (record_dir / "record.json").exists():
        raise FileNotFoundError("Nie znaleziono zapisanej teczki pojazdu.")
    return record_dir


def _latest_evidence(record: dict[str, Any]) -> dict[str, Any]:
    latest = record.get("latest_evidence")
    if isinstance(latest, dict) and latest:
        return latest
    evidences = record.get("evidences")
    if isinstance(evidences, list) and evidences:
        candidate = evidences[-1]
        if isinstance(candidate, dict):
            return candidate
    raise ValueError("Teczka pojazdu nie ma pakietu dowodowego.")


def _safe_child(base_dir: Path, relative_path: str) -> Path:
    root = base_dir.resolve()
    path = (base_dir / relative_path).resolve()
    if root != path and root not in path.parents:
        raise ValueError("Nieprawidłowa ścieżka w teczce pojazdu.")
    return path


def _safe_filename(raw_name: str, fallback: str, ext: str) -> str:
    stem = Path(raw_name or "").stem or fallback
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._-") or fallback
    stem = stem[:70]
    return f"{stem}{ext}"


def _deduped_filename(name: str, used_names: set[str], ext: str) -> str:
    if name not in used_names:
        return name
    stem = Path(name).stem
    duplicate_idx = 2
    while True:
        candidate = f"{stem}_{duplicate_idx}{ext}"
        if candidate not in used_names:
            return candidate
        duplicate_idx += 1


def _image_to_optimized_jpeg(image: Image.Image) -> bytes:
    image = ImageOps.exif_transpose(image)
    if image.mode in {"RGBA", "LA"} or (image.mode == "P" and "transparency" in image.info):
        background = Image.new("RGB", image.size, (255, 255, 255))
        background.paste(image.convert("RGBA"), mask=image.convert("RGBA").split()[-1])
        image = background
    else:
        image = image.convert("RGB")
    image.thumbnail(
        (config.OPTIMIZED_PHOTO_MAX_EDGE_PX, config.OPTIMIZED_PHOTO_MAX_EDGE_PX),
        Image.Resampling.LANCZOS,
    )
    out = io.BytesIO()
    image.save(out, "JPEG", quality=config.OPTIMIZED_PHOTO_JPEG_QUALITY, optimize=True)
    return out.getvalue()


def prepare_report_photos(uploads: list[ReportPhotoUpload]) -> list[PreparedReportPhoto]:
    uploads = [upload for upload in uploads if upload.filename or upload.data]
    if len(uploads) > config.MAX_REPORT_PHOTOS:
        raise ValueError(f"Możesz dodać maksymalnie {config.MAX_REPORT_PHOTOS} zdjęć.")

    prepared: list[PreparedReportPhoto] = []
    used_original_names: set[str] = set()
    for idx, upload in enumerate(uploads, start=1):
        size = len(upload.data)
        if size > config.MAX_REPORT_PHOTO_BYTES:
            raise ValueError(f"Zdjęcie {upload.filename or idx} przekracza limit 10 MB.")
        if size <= 0:
            continue
        try:
            with Image.open(io.BytesIO(upload.data)) as img:
                image_format = str(img.format or "").upper()
                if image_format not in config.ALLOWED_REPORT_PHOTO_EXTENSIONS:
                    raise ValueError("Dozwolone są tylko zdjęcia JPG, PNG albo WebP.")
                optimized = _image_to_optimized_jpeg(img)
        except UnidentifiedImageError as exc:
            raise ValueError(f"Plik {upload.filename or idx} nie jest obsługiwanym zdjęciem.") from exc

        ext = config.ALLOWED_REPORT_PHOTO_EXTENSIONS[image_format]
        base_original_name = _safe_filename(upload.filename, f"zdjecie_{idx:02d}", ext)
        original_name = _deduped_filename(base_original_name, used_original_names, ext)
        used_original_names.add(original_name)
        optimized_name = f"zdjecie_{idx:02d}.jpg"
        prepared.append(
            PreparedReportPhoto(
                original_name=original_name,
                optimized_name=optimized_name,
                original_data=upload.data,
                optimized_data=optimized,
                content_type=upload.content_type,
                size_bytes=size,
                optimized_size_bytes=len(optimized),
            )
        )
    return prepared


def _package_id(wreck_id: str, fields: dict[str, str]) -> str:
    stamp = _now_utc().strftime("%Y%m%dT%H%M%SZ")
    digest = hashlib.sha1(
        f"{wreck_id}:{fields['location_description']}:{stamp}:{secrets.token_urlsafe(8)}".encode("utf-8")
    ).hexdigest()[:8]
    return f"report_{stamp}_{digest}"


def _first_line(value: str, max_len: int = 90) -> str:
    text = " ".join(value.split())
    return text[:max_len].rstrip() or "lokalizacja"


def _labels_text(record: dict[str, Any], evidence: dict[str, Any]) -> str:
    labels = record.get("labels_present") or evidence.get("labels_present") or []
    return ", ".join(str(label) for label in labels) or "brak danych"


def _links_text(links: dict[str, Any]) -> str:
    labels = {
        "street_view": "Google Street View",
        "google_maps_satellite": "Google Maps satelita",
        "apple_maps": "Apple Maps",
        "mapillary": "Mapillary",
        "geoportal": "Geoportal Krajowy",
    }
    lines = []
    for key, label in labels.items():
        url = links.get(key)
        if url:
            lines.append(f"- {label}: {url}")
    return "\n".join(lines) or "- brak linków"


def build_mail_draft(record: dict[str, Any], evidence: dict[str, Any], fields: dict[str, str]) -> tuple[str, str]:
    lat = float(record.get("lat"))
    lon = float(record.get("lon"))
    labels = _labels_text(record, evidence)
    score = float(record.get("best_score") or evidence.get("score") or 0.0)
    links = record.get("links") or evidence.get("links") or {}
    subject = f"Zgłoszenie pojazdu nieużytkowanego - {_first_line(fields['location_description'])}"
    body = f"""Dzień dobry,

zgłaszam pojazd, który wygląda na długotrwale nieużytkowany.

Dane osoby zgłaszającej:
- Imię i nazwisko: {fields["reporter_name"]}
- Adres zamieszkania: {fields["reporter_address"]}
- Telefon: {fields["reporter_phone"]}
- E-mail: {fields["reporter_email"]}

Miejsce pojazdu:
{fields["location_description"]}

Współrzędne GPS:
{lat:.6f}, {lon:.6f}

Data i godzina obserwacji:
{fields["observed_at"]}

Opis stanu pojazdu:
{fields["vehicle_description"]}

Materiał pomocniczy z aplikacji WreckScanner:
- lokalna teczka: {record.get("id")}
- pojazd widoczny na ortofotomapach z lat: {labels}
- najlepszy score analizy: {score * 100:.0f}%

Linki do weryfikacji miejsca:
{_links_text(links)}

W załączniku dołączam pakiet dowodowy ZIP z miniaturami historycznymi, zdjęciami z miejsca oraz metadanymi analizy. Proszę o weryfikację przez patrol i podjęcie czynności w sprawie pojazdu nieużytkowanego.

Z poważaniem,
{fields["reporter_name"]}
"""
    return subject, body


def _fallback_report_html(record: dict[str, Any]) -> bytes:
    title = f"Teczka pojazdu {record.get('id', '')}"
    labels = ", ".join(str(label) for label in record.get("labels_present") or [])
    body = f"""<!doctype html>
<html lang="pl">
<head><meta charset="utf-8"><title>{html.escape(title)}</title></head>
<body>
<h1>{html.escape(title)}</h1>
<p>{float(record.get("lat") or 0):.6f}, {float(record.get("lon") or 0):.6f}</p>
<p>Widziane: {html.escape(labels or "brak danych")}</p>
</body>
</html>
"""
    return body.encode("utf-8")


def _mail_draft_html_section(recipient: str, subject: str, mail_body: str) -> str:
    return f"""
<section class="evidence report-mail-draft">
<h2>Treść zgłoszenia</h2>
<dl>
<dt>Adresat</dt>
<dd>{html.escape(recipient)}</dd>
<dt>Temat</dt>
<dd>{html.escape(subject)}</dd>
</dl>
<pre>{html.escape(mail_body)}</pre>
</section>
"""


def _report_package_style() -> str:
    return """
<style data-report-package-style>
  .report-mail-draft pre {
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    word-break: break-word;
    max-width: 100%;
    box-sizing: border-box;
  }
  .report-package-photos .grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
    gap: 10px;
  }
  .report-package-photos figure {
    margin: 0;
    border: 1px solid var(--bdr, #1f2937);
    border-radius: 8px;
    overflow: hidden;
    background: #0f172a;
  }
  .report-package-photos img {
    width: 100%;
    aspect-ratio: 1;
    object-fit: cover;
    display: block;
  }
  .report-package-photos figcaption {
    padding: 8px;
    color: var(--mut, #94a3b8);
    font-size: 12px;
    text-align: center;
    overflow-wrap: anywhere;
  }
</style>
"""


def _strip_interactive_report_controls(html_text: str) -> str:
    html_text = re.sub(
        r"\s*<section class=\"evidence admin-upload\" data-report-admin-upload>.*?</section>",
        "",
        html_text,
        flags=re.DOTALL,
    )
    return re.sub(r"\s*<script data-report-admin-script>.*?</script>", "", html_text, flags=re.DOTALL)


def _inject_report_package_style(html_text: str) -> str:
    if "data-report-package-style" in html_text:
        return html_text
    style = _report_package_style()
    lower_html = html_text.lower()
    idx = lower_html.rfind("</head>")
    if idx != -1:
        return f"{html_text[:idx]}{style}{html_text[idx:]}"
    return f"{style}{html_text}"


def _report_package_photos_section(photos: list[PreparedReportPhoto]) -> str:
    if not photos:
        return ""
    figures = []
    for photo in photos:
        figures.append(
            f"""
            <figure>
              <img src="zdjecia_z_miejsca/{html.escape(photo.optimized_name)}" loading="lazy" alt="">
              <figcaption>{html.escape(photo.original_name)}</figcaption>
            </figure>
            """
        )
    return f"""
<section class="evidence report-package-photos">
<h2>Zdjęcia dołączone do zgłoszenia</h2>
<div class="grid">{"".join(figures)}</div>
</section>
"""


def _report_html_with_mail_draft(
    record_dir: Path,
    record: dict[str, Any],
    subject: str,
    mail_body: str,
    photos: list[PreparedReportPhoto],
) -> bytes:
    report_html = record_dir / "index.html"
    if report_html.exists():
        html_text = report_html.read_text(encoding="utf-8")
    else:
        html_text = _fallback_report_html(record).decode("utf-8")

    html_text = _inject_report_package_style(_strip_interactive_report_controls(html_text))
    section = _report_package_photos_section(photos) + _mail_draft_html_section(
        config.REPORT_RECIPIENT, subject, mail_body
    )
    lower_html = html_text.lower()
    for marker in ("</main>", "</body>"):
        idx = lower_html.rfind(marker)
        if idx != -1:
            html_text = f"{html_text[:idx]}{section}{html_text[idx:]}"
            break
    else:
        html_text = f"{html_text}\n{section}"
    return html_text.encode("utf-8")


def _archive_attached_photos(archive: zipfile.ZipFile, record_dir: Path, record: dict[str, Any]) -> None:
    photos = record.get("attached_photos") if isinstance(record.get("attached_photos"), list) else []
    for photo in photos:
        if not isinstance(photo, dict) or not is_approved(photo):
            continue
        for key in ("public_thumb_file", "public_image_file"):
            rel = str(photo.get(key) or "")
            if not rel:
                continue
            path = _safe_child(record_dir, rel)
            if path.exists():
                archive.write(path, rel)


def _archive_public_evidence_photos(
    archive: zipfile.ZipFile,
    record_dir: Path,
    evidence: dict[str, Any],
    *,
    archive_root: str = "miniatury_historyczne",
) -> None:
    evidence_dir = _safe_child(record_dir, str(evidence.get("path") or ""))
    for crop in evidence.get("crops") or []:
        if not isinstance(crop, dict):
            continue
        label = _safe_filename(str(crop.get("label") or "miniatura"), "miniatura", ".jpg")
        crop_path = evidence_dir / str(crop.get("file") or "")
        if crop_path.exists():
            archive.write(crop_path, f"{archive_root}/{label}")


def _public_report_html(
    record: dict[str, Any],
    evidence: dict[str, Any],
    subject: str,
    mail_body: str,
    photos: list[PreparedReportPhoto],
) -> bytes:
    attached = record.get("attached_photos") if isinstance(record.get("attached_photos"), list) else []
    attached_figures = []
    for photo in attached:
        if not isinstance(photo, dict) or not is_approved(photo):
            continue
        rel = html.escape(str(photo.get("public_image_file") or photo.get("public_thumb_file") or ""))
        if rel:
            attached_figures.append(f'<figure><img src="{rel}" alt=""><figcaption>Zdjęcie z teczki pojazdu</figcaption></figure>')

    crop_figures = []
    for crop in evidence.get("crops") or []:
        if not isinstance(crop, dict):
            continue
        label = _safe_filename(str(crop.get("label") or "miniatura"), "miniatura", ".jpg")
        crop_figures.append(
            f'<figure><img src="miniatury_historyczne/{html.escape(label)}" alt=""><figcaption>{html.escape(str(crop.get("label") or ""))}</figcaption></figure>'
        )

    user_figures = [
        f'<figure><img src="zdjecia_z_miejsca/{html.escape(photo.optimized_name)}" alt=""><figcaption>{html.escape(photo.original_name)}</figcaption></figure>'
        for photo in photos
    ]
    gallery = "".join(crop_figures + attached_figures + user_figures) or "<p>Brak zdjęć w pakiecie.</p>"
    body = f"""<!doctype html>
<html lang="pl">
<head>
<meta charset="utf-8">
<title>{html.escape(subject)}</title>
<style>
body {{ font-family: system-ui, sans-serif; color: #0f172a; margin: 32px; line-height: 1.55; }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; }}
figure {{ margin: 0; border: 1px solid #cbd5e1; border-radius: 8px; overflow: hidden; }}
img {{ width: 100%; aspect-ratio: 1; object-fit: cover; display: block; }}
figcaption {{ padding: 8px; color: #475569; font-size: 12px; }}
pre {{ white-space: pre-wrap; overflow-wrap: anywhere; background: #f8fafc; border: 1px solid #cbd5e1; border-radius: 8px; padding: 14px; }}
</style>
</head>
<body>
<h1>{html.escape(subject)}</h1>
<p>Teczka pojazdu: {html.escape(str(record.get("id") or ""))}</p>
<p>Współrzędne: {float(record.get("lat") or 0):.6f}, {float(record.get("lon") or 0):.6f}</p>
<h2>Zdjęcia publiczne i dołączone</h2>
<div class="grid">{gallery}</div>
<h2>Treść zgłoszenia</h2>
<pre>{html.escape(mail_body)}</pre>
</body>
</html>
"""
    return body.encode("utf-8")


def _write_public_zip(
    zip_path: Path,
    record_dir: Path,
    record: dict[str, Any],
    evidence: dict[str, Any],
    mail_body: str,
    subject: str,
    photos: list[PreparedReportPhoto],
) -> None:
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("zgloszenie.txt", f"Do: {config.REPORT_RECIPIENT}\nTemat: {subject}\n\n{mail_body}")
        archive.writestr("raport.html", _public_report_html(record, evidence, subject, mail_body, photos))
        _archive_attached_photos(archive, record_dir, record)
        _archive_public_evidence_photos(archive, record_dir, evidence)
        for photo in photos:
            archive.writestr(f"zdjecia_z_miejsca/{photo.optimized_name}", photo.optimized_data)


def _write_zip(
    zip_path: Path,
    record_dir: Path,
    record: dict[str, Any],
    evidence: dict[str, Any],
    mail_body: str,
    subject: str,
    photos: list[PreparedReportPhoto],
) -> None:
    evidence_rel = str(evidence.get("path") or "")
    evidence_dir = _safe_child(record_dir, evidence_rel)
    evidence_archive_root = "/".join(part for part in Path(evidence_rel).parts if part and part not in {".", ".."})
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("zgloszenie.txt", f"Do: {config.REPORT_RECIPIENT}\nTemat: {subject}\n\n{mail_body}")
        archive.writestr("raport.html", _report_html_with_mail_draft(record_dir, record, subject, mail_body, photos))

        archive.write(record_dir / "record.json", "metadane/record.json")
        _archive_attached_photos(archive, record_dir, record)
        for file_name in ("candidate.json", "metadata.json", "links.json"):
            path = evidence_dir / file_name
            if path.exists():
                archive.write(path, f"metadane/{file_name}")

        if evidence_archive_root:
            for path in sorted(evidence_dir.rglob("*")):
                if path.is_file():
                    archive.write(path, f"{evidence_archive_root}/{path.relative_to(evidence_dir).as_posix()}")

        _archive_public_evidence_photos(archive, record_dir, evidence)

        for photo in photos:
            archive.writestr(f"zdjecia_z_miejsca/{photo.optimized_name}", photo.optimized_data)


def _write_pdf(
    pdf_path: Path,
    record_dir: Path,
    record: dict[str, Any],
    evidence: dict[str, Any],
    mail_body: str,
    subject: str,
    photos: list[PreparedReportPhoto],
) -> None:
    report_photos = [PdfPhoto(label=photo.original_name, data=photo.optimized_data) for photo in photos]
    body = build_report_pdf(
        record=record,
        evidence=evidence,
        record_dir=record_dir,
        recipient=config.REPORT_RECIPIENT,
        subject=subject,
        mail_body=mail_body,
        report_photos=report_photos,
    )
    pdf_path.write_bytes(body)


def create_report_package(
    wreck_id: str,
    fields: dict[str, str],
    photos: list[ReportPhotoUpload],
    wrecks_dir: Path,
) -> dict[str, Any]:
    fields = _validate_fields(fields)
    prepared_photos = prepare_report_photos(photos)
    record_dir = _record_dir_for(wreck_id, wrecks_dir)
    record = _read_json(record_dir / "record.json")
    if not isinstance(record, dict):
        raise ValueError("Nieprawidłowy format record.json.")
    evidence = _latest_evidence(record)
    render_wreck_record_html(record, record_dir)
    subject, mail_body = build_mail_draft(record, evidence, fields)

    package_id = _package_id(wreck_id, fields)
    reports_dir = config.PRIVATE_REPORTS_DIR / str(record["id"])
    package_dir = reports_dir / package_id
    originals_dir = package_dir / "oryginalne_zdjecia"
    optimized_dir = package_dir / "zdjecia_do_maila"
    zip_path = reports_dir / f"{package_id}.zip"
    pdf_path = reports_dir / f"{package_id}.pdf"
    originals_dir.mkdir(parents=True, exist_ok=False)
    optimized_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)

    for photo in prepared_photos:
        (originals_dir / photo.original_name).write_bytes(photo.original_data)
        (optimized_dir / photo.optimized_name).write_bytes(photo.optimized_data)

    _write_zip(zip_path, record_dir, record, evidence, mail_body, subject, prepared_photos)
    _write_pdf(pdf_path, record_dir, record, evidence, mail_body, subject, prepared_photos)
    return {
        "status": "ok",
        "recipient": config.REPORT_RECIPIENT,
        "subject": subject,
        "body": mail_body,
        "package_id": package_id,
        "zip_url": f"/api/report-packages/{record['id']}/{package_id}/zip",
        "pdf_url": f"/api/report-packages/{record['id']}/{package_id}/pdf",
        "photo_count": len(prepared_photos),
        "zip_size_bytes": zip_path.stat().st_size,
        "pdf_size_bytes": pdf_path.stat().st_size,
    }


def _new_access_token() -> ReportPackageAccess:
    now = _now_utc()
    expires_at = now + timedelta(seconds=config.PUBLIC_REPORT_PACKAGE_TOKEN_TTL_SECONDS)
    return ReportPackageAccess(token=secrets.token_urlsafe(24), expires_at=_iso(expires_at))


def create_public_report_package(
    wreck_id: str,
    fields: dict[str, str],
    photos: list[ReportPhotoUpload],
    wrecks_dir: Path,
) -> dict[str, Any]:
    fields = _validate_fields(fields)
    prepared_photos = prepare_report_photos(photos)
    record_dir = _record_dir_for(wreck_id, wrecks_dir)
    record = _read_json(record_dir / "record.json")
    if not isinstance(record, dict):
        raise ValueError("Nieprawidłowy format record.json.")
    evidence = _latest_evidence(record)
    render_wreck_record_html(record, record_dir)
    subject, mail_body = build_mail_draft(record, evidence, fields)

    package_id = _package_id(wreck_id, fields)
    reports_dir = config.PRIVATE_REPORTS_DIR / str(record["id"])
    package_dir = reports_dir / package_id
    optimized_dir = package_dir / "zdjecia_do_maila"
    zip_path = reports_dir / f"{package_id}.zip"
    pdf_path = reports_dir / f"{package_id}.pdf"
    access_path = reports_dir / f"{package_id}.access.json"
    optimized_dir.mkdir(parents=True, exist_ok=False)
    reports_dir.mkdir(parents=True, exist_ok=True)

    for photo in prepared_photos:
        (optimized_dir / photo.optimized_name).write_bytes(photo.optimized_data)

    _write_public_zip(zip_path, record_dir, record, evidence, mail_body, subject, prepared_photos)
    _write_pdf(pdf_path, record_dir, record, evidence, mail_body, subject, prepared_photos)
    access = _new_access_token()
    _write_json(
        access_path,
        {
            "package_id": package_id,
            "token": access.token,
            "expires_at": access.expires_at,
            "created_at": _iso(_now_utc()),
            "scope": "public_clean_report",
        },
    )
    token_query = f"?token={access.token}"
    return {
        "status": "ok",
        "recipient": config.REPORT_RECIPIENT,
        "subject": subject,
        "body": mail_body,
        "package_id": package_id,
        "zip_url": f"/api/public-report-packages/{record['id']}/{package_id}/zip{token_query}",
        "pdf_url": f"/api/public-report-packages/{record['id']}/{package_id}/pdf{token_query}",
        "expires_at": access.expires_at,
        "photo_count": len(prepared_photos),
        "zip_size_bytes": zip_path.stat().st_size,
        "pdf_size_bytes": pdf_path.stat().st_size,
    }


def report_package_asset(wreck_id: str, package_id: str, asset: str) -> tuple[Path, str]:
    wreck_id = _validate_wreck_id(wreck_id)
    if not re.fullmatch(r"report_\d{8}T\d{6}Z_[a-f0-9]{8}", str(package_id or "")):
        raise ValueError("Nieprawidłowy identyfikator pakietu zgłoszenia.")
    if asset == "zip":
        path = config.PRIVATE_REPORTS_DIR / wreck_id / f"{package_id}.zip"
        content_type = "application/zip"
    elif asset == "pdf":
        path = config.PRIVATE_REPORTS_DIR / wreck_id / f"{package_id}.pdf"
        content_type = "application/pdf"
    else:
        raise ValueError("Nieprawidłowy typ pliku pakietu zgłoszenia.")
    root = config.PRIVATE_REPORTS_DIR.resolve()
    resolved = path.resolve()
    if root != resolved and root not in resolved.parents:
        raise ValueError("Nieprawidłowa ścieżka pakietu zgłoszenia.")
    if not resolved.exists():
        raise FileNotFoundError("Nie znaleziono pakietu zgłoszenia.")
    return resolved, content_type


def public_report_package_asset(wreck_id: str, package_id: str, asset: str, token: str) -> tuple[Path, str]:
    path, content_type = report_package_asset(wreck_id, package_id, asset)
    access_path = config.PRIVATE_REPORTS_DIR / wreck_id / f"{package_id}.access.json"
    try:
        access = _read_json(access_path)
    except (OSError, json.JSONDecodeError) as exc:
        raise FileNotFoundError("Nie znaleziono publicznego dostępu do pakietu.") from exc
    if not isinstance(access, dict) or access.get("scope") != "public_clean_report":
        raise FileNotFoundError("Nie znaleziono publicznego dostępu do pakietu.")
    if not secrets.compare_digest(str(access.get("token") or ""), str(token or "")):
        raise FileNotFoundError("Nie znaleziono publicznego dostępu do pakietu.")
    expires_text = str(access.get("expires_at") or "").replace("Z", "+00:00")
    try:
        expires_at = datetime.fromisoformat(expires_text)
    except ValueError as exc:
        raise FileNotFoundError("Publiczny dostęp do pakietu wygasł.") from exc
    if expires_at < _now_utc():
        raise FileNotFoundError("Publiczny dostęp do pakietu wygasł.")
    return path, content_type
