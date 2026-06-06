from __future__ import annotations

import html
import io
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError

from core.photo_privacy import is_approved

PAGE_WIDTH = 1240
PAGE_HEIGHT = 1754
PAGE_MARGIN = 70
PAGE_BG = (248, 250, 252)
CARD_BG = (255, 255, 255)
CARD_BORDER = (203, 213, 225)
TEXT = (15, 23, 42)
MUTED = (71, 85, 105)
ACCENT = (5, 150, 105)
LINK = (37, 99, 235)


@dataclass(frozen=True)
class PdfPhoto:
    label: str
    data: bytes


def _font(size: int, *, bold: bool = False) -> ImageFont.ImageFont:
    names = (
        ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"),
        ("Arial Bold.ttf" if bold else "Arial.ttf"),
    )
    roots = (
        Path("/usr/share/fonts/truetype/dejavu"),
        Path("/usr/share/fonts/truetype/msttcorefonts"),
        Path("/Library/Fonts"),
        Path("C:/Windows/Fonts"),
    )
    for root in roots:
        for name in names:
            path = root / name
            if path.exists():
                return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


def _text_width(text: str, font: ImageFont.ImageFont) -> int:
    box = font.getbbox(text)
    return int(box[2] - box[0])


def _line_height(font: ImageFont.ImageFont, spacing: int = 8) -> int:
    box = font.getbbox("Ag")
    return int(box[3] - box[1]) + spacing


def _break_long_word(word: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
    parts: list[str] = []
    current = ""
    for char in word:
        candidate = f"{current}{char}"
        if current and _text_width(candidate, font) > max_width:
            parts.append(current)
            current = char
        else:
            current = candidate
    if current:
        parts.append(current)
    return parts or [word]


def _wrap_line(text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        word_parts = _break_long_word(word, font, max_width) if _text_width(word, font) > max_width else [word]
        for part in word_parts:
            candidate = part if not current else f"{current} {part}"
            if current and _text_width(candidate, font) > max_width:
                lines.append(current)
                current = part
            else:
                current = candidate
    if current:
        lines.append(current)
    return lines or [""]


def _wrap_text(text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
    lines: list[str] = []
    for raw_line in str(text or "").replace("\r\n", "\n").split("\n"):
        if raw_line.strip():
            lines.extend(_wrap_line(raw_line, font, max_width))
        else:
            lines.append("")
    return lines


def _safe_child(base_dir: Path, relative_path: str) -> Path:
    root = base_dir.resolve()
    path = (base_dir / relative_path).resolve()
    if root != path and root not in path.parents:
        raise ValueError("Nieprawidłowa ścieżka w teczce pojazdu.")
    return path


def _compact_years(labels: list[Any]) -> str:
    years = sorted(int(label) for label in labels if str(label).isdigit())
    if not years:
        return "brak danych"
    if len(years) >= 3:
        return f"{years[0]}-{years[-1]} ({len(years)} lat)"
    return ", ".join(str(year) for year in years)


def _compact_datetime(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "brak danych"
    return text.replace("T", " ").removesuffix("Z")


def _links_text(links: dict[str, Any]) -> str:
    labels = {
        "street_view": "Street View",
        "google_maps_satellite": "Google Maps satelita",
        "apple_maps": "Apple Maps",
        "mapillary": "Mapillary",
        "geoportal": "Geoportal",
    }
    lines = []
    for key, label in labels.items():
        url = links.get(key)
        if url:
            lines.append(f"{label}: {url}")
    return "\n".join(lines) or "brak linków"


class _PdfPages:
    def __init__(self) -> None:
        self.pages: list[Image.Image] = []
        self.title_font = _font(34, bold=True)
        self.heading_font = _font(25, bold=True)
        self.label_font = _font(17, bold=True)
        self.body_font = _font(19)
        self.small_font = _font(15)
        self._new_page()

    @property
    def page(self) -> Image.Image:
        return self.pages[-1]

    @property
    def draw(self) -> ImageDraw.ImageDraw:
        return ImageDraw.Draw(self.page)

    @property
    def bottom(self) -> int:
        return PAGE_HEIGHT - PAGE_MARGIN

    @property
    def content_width(self) -> int:
        return PAGE_WIDTH - (PAGE_MARGIN * 2)

    def _new_page(self) -> None:
        self.pages.append(Image.new("RGB", (PAGE_WIDTH, PAGE_HEIGHT), PAGE_BG))
        self.y = PAGE_MARGIN

    def page_break(self) -> None:
        if self.y > PAGE_MARGIN:
            self._new_page()

    def _ensure(self, height: int) -> None:
        if self.y + height > self.bottom:
            self._new_page()

    def title(self, text: str) -> None:
        lines = _wrap_text(text, self.title_font, self.content_width)
        height = len(lines) * _line_height(self.title_font, 10) + 12
        self._ensure(height)
        for line in lines:
            self.draw.text((PAGE_MARGIN, self.y), line, font=self.title_font, fill=TEXT)
            self.y += _line_height(self.title_font, 10)
        self.y += 12

    def heading(self, text: str) -> None:
        self._ensure(54)
        self.draw.text((PAGE_MARGIN, self.y), text, font=self.heading_font, fill=TEXT)
        self.y += 44

    def paragraph(
        self, text: str, *, fill: tuple[int, int, int] = TEXT, font: ImageFont.ImageFont | None = None
    ) -> None:
        font = font or self.body_font
        line_height = _line_height(font)
        for line in _wrap_text(text, font, self.content_width):
            self._ensure(line_height)
            if line:
                self.draw.text((PAGE_MARGIN, self.y), line, font=font, fill=fill)
            self.y += line_height
        self.y += 10

    def key_values(self, items: list[tuple[str, str]]) -> None:
        row_gap = 8
        x = PAGE_MARGIN
        max_x = PAGE_WIDTH - PAGE_MARGIN
        pill_height = 36
        self._ensure(pill_height + row_gap)
        for label, value in items:
            text = f"{label}: {value}"
            width = min(_text_width(text, self.small_font) + 26, self.content_width)
            if x + width > max_x:
                x = PAGE_MARGIN
                self.y += pill_height + row_gap
                self._ensure(pill_height + row_gap)
            self.draw.rounded_rectangle(
                (x, self.y, x + width, self.y + pill_height),
                radius=14,
                fill=CARD_BG,
                outline=CARD_BORDER,
                width=1,
            )
            display = text
            while _text_width(display, self.small_font) > width - 22 and len(display) > 4:
                display = f"{display[:-4]}..."
            self.draw.text((x + 13, self.y + 9), display, font=self.small_font, fill=MUTED)
            x += width + 8
        self.y += pill_height + 18

    def image_grid(self, photos: list[PdfPhoto], *, columns: int = 2) -> None:
        if not photos:
            return
        gap = 18
        cell_w = math.floor((self.content_width - (gap * (columns - 1))) / columns)
        image_h = 280
        label_h = 44
        cell_h = image_h + label_h
        x_positions = [PAGE_MARGIN + idx * (cell_w + gap) for idx in range(columns)]

        col = 0
        for photo in photos:
            if col == 0:
                self._ensure(cell_h + gap)
            x = x_positions[col]
            y = self.y
            self.draw.rounded_rectangle(
                (x, y, x + cell_w, y + cell_h),
                radius=12,
                fill=CARD_BG,
                outline=CARD_BORDER,
                width=1,
            )
            try:
                with Image.open(io.BytesIO(photo.data)) as raw:
                    image = ImageOps.exif_transpose(raw).convert("RGB")
                    image.thumbnail((cell_w - 16, image_h - 16), Image.Resampling.LANCZOS)
                    paste_x = x + (cell_w - image.width) // 2
                    paste_y = y + 8 + (image_h - 16 - image.height) // 2
                    self.page.paste(image, (paste_x, paste_y))
            except (OSError, UnidentifiedImageError):
                self.draw.text((x + 14, y + 120), "Nie można odczytać zdjęcia", font=self.small_font, fill=MUTED)

            label = html.unescape(photo.label or "zdjęcie")
            label_lines = _wrap_text(label, self.small_font, cell_w - 20)[:2]
            label_y = y + image_h + 8
            for line in label_lines:
                self.draw.text((x + 10, label_y), line, font=self.small_font, fill=MUTED)
                label_y += _line_height(self.small_font, 2)
            col += 1
            if col >= columns:
                col = 0
                self.y += cell_h + gap
        if col:
            self.y += cell_h + gap
        self.y += 8

    def to_pdf(self) -> bytes:
        out = io.BytesIO()
        first, *rest = self.pages
        first.save(out, "PDF", save_all=True, append_images=rest, resolution=150.0)
        return out.getvalue()


def _photo_bytes_from_record(record_dir: Path, photo: dict[str, Any]) -> PdfPhoto | None:
    if not is_approved(photo):
        return None
    public_rel = str(photo.get("public_image_file") or "")
    thumb_rel = str(photo.get("public_thumb_file") or "")
    rel = public_rel or thumb_rel
    if not rel:
        return None
    path = _safe_child(record_dir, rel)
    if not path.exists():
        return None
    label = str(photo.get("original_filename") or photo.get("id") or "zdjęcie z miejsca")
    return PdfPhoto(label=label, data=path.read_bytes())


def _attached_photos(record: dict[str, Any], record_dir: Path) -> list[PdfPhoto]:
    photos = record.get("attached_photos") if isinstance(record.get("attached_photos"), list) else []
    prepared = []
    for photo in photos:
        if isinstance(photo, dict):
            pdf_photo = _photo_bytes_from_record(record_dir, photo)
            if pdf_photo:
                prepared.append(pdf_photo)
    return prepared


def _evidence_photos(evidence: dict[str, Any], record_dir: Path) -> list[PdfPhoto]:
    evidence_rel = str(evidence.get("path") or "")
    evidence_dir = _safe_child(record_dir, evidence_rel)
    prepared = []
    for crop in evidence.get("crops") or []:
        if not isinstance(crop, dict):
            continue
        rel = str(crop.get("file") or "")
        if not rel:
            continue
        path = _safe_child(evidence_dir, rel)
        if path.exists():
            prepared.append(PdfPhoto(label=str(crop.get("label") or path.stem), data=path.read_bytes()))
    return prepared


def build_report_pdf(
    *,
    record: dict[str, Any],
    evidence: dict[str, Any],
    record_dir: Path,
    recipient: str,
    subject: str,
    mail_body: str,
    report_photos: list[PdfPhoto],
) -> bytes:
    doc = _PdfPages()
    latest = record.get("latest_evidence") if isinstance(record.get("latest_evidence"), dict) else {}
    score = float(record.get("best_score") or evidence.get("score") or 0.0)
    evidences = record.get("evidences") if isinstance(record.get("evidences"), list) else []
    attached_photos = _attached_photos(record, record_dir)

    doc.title(f"Teczka pojazdu {record.get('id', '')}")
    doc.key_values(
        [
            ("Status", str(record.get("status", "confirmed"))),
            ("GPS", f"{float(record.get('lat') or 0):.6f}, {float(record.get('lon') or 0):.6f}"),
            ("Score", f"{score * 100:.0f}%"),
            ("Widziane", _compact_years(record.get("labels_present") or evidence.get("labels_present") or [])),
            ("Dowody", str(len(evidences))),
            ("Zdjęcia", str(len(attached_photos) + len(report_photos))),
            ("Ostatni dowód", _compact_datetime(latest.get("created_at"))),
        ]
    )

    doc.heading("Linki do weryfikacji")
    doc.paragraph(_links_text(record.get("links") or evidence.get("links") or {}), fill=LINK, font=doc.small_font)

    if attached_photos:
        doc.heading("Zdjęcia z miejsca")
        doc.image_grid(attached_photos)

    evidence_images = _evidence_photos(evidence, record_dir)
    if evidence_images:
        doc.heading("Miniatury historyczne")
        doc.image_grid(evidence_images, columns=3)

    if report_photos:
        doc.heading("Zdjęcia dołączone do zgłoszenia")
        doc.image_grid(report_photos)

    doc.page_break()
    doc.heading("Treść zgłoszenia")
    doc.key_values([("Adresat", recipient), ("Temat", subject)])
    doc.paragraph(mail_body)
    return doc.to_pdf()
