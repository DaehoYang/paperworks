#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import re
from dataclasses import dataclass
from datetime import date, datetime
from io import BytesIO
from pathlib import Path

import xlwt
import yaml
from PIL import Image, ImageOps
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, BooleanObject, DictionaryObject, NameObject, TextStringObject
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from ..common import document_reader as doc_reader
from ..common import validators as common_validators
from .create_inspection_form import add_inspection_fields


logging.getLogger("pypdf").setLevel(logging.ERROR)

PACKAGE_DIR = Path(__file__).resolve().parents[1]
WORKSPACE_DIR = Path(__file__).resolve().parents[3]
ASSETS_DIR = PACKAGE_DIR / "assets"
PURCHASE_DIR = WORKSPACE_DIR / "purchase"
DEFAULT_PURCHASE_DIR = PURCHASE_DIR / "260401_optics"
DEFAULT_FORM_TEMPLATE = ASSETS_DIR / "물품검수확인서_입력가능.pdf"
DEFAULT_OCR_API_URL = "https://dhlab.gachon.ac.kr/services/rag/ocr"
DEFAULT_LITELLM_BASE_URL = "https://dhlab.gachon.ac.kr/services/litellm/v1"
DEFAULT_PROJECTS_YML = WORKSPACE_DIR / "projects.yml"

ITEM_LINE = re.compile(
    r"^\s*(?P<number>\d+)\s*(?P<desc>.*?)\s{2,}"
    r"(?P<model>[A-Za-z0-9][A-Za-z0-9-]+)\s+"
    r"(?P<quantity>\d+)\s+"
    r"(?P<unit_price>[\d,]+)\s+"
    r"(?P<supply_price>[\d,]+)\s*$"
)

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"}
QUOTE_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"}
QUOTE_NAME_TOKENS = ("견적서", "견적")
IMAGE_DIR_CANDIDATES = ("imgs", "imgs1", "img")
PHOTO_RECTS = [
    (55, 417, 270, 625),
    (304, 417, 519, 625),
    (55, 169, 270, 377),
    (304, 169, 519, 377),
]
@dataclass
class PurchaseItem:
    number: int
    name: str
    specification: str
    model: str
    quantity: int
    # VAT-included price for one item, normalized for Ezbaro item upload.
    unit_price: int
    # VAT-excluded total price for the purchased quantity, normalized for Ezbaro item upload.
    supply_price: int

    @property
    def vat(self) -> int:
        return int(round(self.supply_price * 0.1))

    @property
    def total_price(self) -> int:
        return self.supply_price + self.vat


@dataclass
class QuoteTotals:
    supply_price: int | None = None
    vat: int | None = None
    total_price: int | None = None


def int_price(value: str) -> int:
    return int(value.replace(",", "").strip())


def quote_llm_prompt() -> str:
    return (
        "Extract purchase quote table data from Korean quote text. Return only compact JSON with this schema: "
        "{\"items\":[{\"number\":1,\"name\":\"item name for inspection form\",\"specification\":\"full specification\","
        "\"model\":\"model name\",\"quantity\":1,\"unit_price_raw\":1000,\"amount_raw\":1000}],"
        "\"totals\":{\"supply_price\":1000,\"vat\":100,\"total_price\":1100}}. "
        "unit_price_raw is the value printed in the unit price column. amount_raw is the value printed in the amount/supply/total column. "
        "Do not normalize VAT yourself; preserve printed raw table values. Use integers without commas. "
        "If a value is absent, use null. Do not include markdown."
    )

def parse_quote_totals(text: str) -> QuoteTotals:
    totals = QuoteTotals()
    for line in text.splitlines():
        compact = line.replace(" ", "")
        if "공급가액" in line and "VAT" in line and "합계" in line:
            prices = [int_price(value) for value in re.findall(r"\d[\d,]{2,}", line)]
            if len(prices) >= 3:
                totals.supply_price, totals.vat, totals.total_price = prices[:3]
        elif "합계금액" in compact:
            prices = [int_price(value) for value in re.findall(r"\d[\d,]{2,}", line)]
            if prices:
                totals.total_price = prices[-1]
    return totals


def quote_total_score(items: list[PurchaseItem], totals: QuoteTotals) -> int:
    score = 0
    if totals.supply_price is not None:
        score += abs(sum(item.supply_price for item in items) - totals.supply_price)
    if totals.vat is not None:
        score += abs(sum(item.vat for item in items) - totals.vat)
    if totals.total_price is not None:
        score += abs(sum(item.total_price for item in items) - totals.total_price)
    return score


def supply_from_vat_included(value: int) -> int:
    return int(round(value / 1.1))


def normalize_prices(items: list[PurchaseItem], raw_prices: list[tuple[int, int]], totals: QuoteTotals) -> tuple[list[PurchaseItem], str]:
    candidates: list[tuple[str, list[PurchaseItem]]] = []

    def clone_with_prices(mode: str) -> None:
        normalized: list[PurchaseItem] = []
        for item, (raw_unit, raw_amount) in zip(items, raw_prices):
            if mode == "amount_excluded_quantity_total":
                supply = raw_amount
                unit = int(round((supply / item.quantity) * 1.1))
            elif mode == "amount_included_quantity_total":
                unit = int(round(raw_amount / item.quantity))
                supply = supply_from_vat_included(unit) * item.quantity
            elif mode == "amount_excluded_unit_price":
                supply = raw_amount * item.quantity
                unit = int(round(raw_amount * 1.1))
            elif mode == "amount_included_unit_price":
                unit = raw_amount
                supply = supply_from_vat_included(unit) * item.quantity
            elif mode == "unit_excluded_unit_price":
                supply = raw_unit * item.quantity
                unit = int(round(raw_unit * 1.1))
            elif mode == "unit_included_unit_price":
                unit = raw_unit
                supply = supply_from_vat_included(unit) * item.quantity
            else:
                raise ValueError(mode)
            normalized.append(
                PurchaseItem(
                    number=item.number,
                    name=item.name,
                    specification=item.specification,
                    model=item.model,
                    quantity=item.quantity,
                    unit_price=unit,
                    supply_price=supply,
                )
            )
        candidates.append((mode, normalized))

    for mode_name in (
        "amount_excluded_quantity_total",
        "amount_included_quantity_total",
        "amount_excluded_unit_price",
        "amount_included_unit_price",
        "unit_excluded_unit_price",
        "unit_included_unit_price",
    ):
        clone_with_prices(mode_name)

    best_mode, best_items = min(candidates, key=lambda candidate: quote_total_score(candidate[1], totals))
    score = quote_total_score(best_items, totals)
    if (totals.supply_price is not None or totals.total_price is not None) and score > max(1000, int((totals.total_price or totals.supply_price or 0) * 0.02)):
        raise ValueError(f"Could not reconcile quote item prices with totals. best_mode={best_mode}, score={score}, totals={totals}")
    return best_items, best_mode


def parse_quote_items_from_text(text: str) -> tuple[list[PurchaseItem], str, QuoteTotals]:
    totals = parse_quote_totals(text)
    lines = text.splitlines()
    items: list[PurchaseItem] = []
    raw_prices: list[tuple[int, int]] = []
    pending_desc: list[str] = []
    current: PurchaseItem | None = None
    in_table = False

    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if "번호" in line and "모델명" in line and "공급가액" in line:
            in_table = True
            continue
        if not in_table:
            continue
        if line.startswith("* Note") or line.startswith("공급가액"):
            break

        match = ITEM_LINE.match(raw)
        if match:
            desc = " ".join([*pending_desc, match.group("desc").strip()]).strip()
            model = match.group("model").strip()
            current = PurchaseItem(
                number=int(match.group("number")),
                name=model,
                specification=desc,
                model=model,
                quantity=int(match.group("quantity")),
                unit_price=0,
                supply_price=0,
            )
            items.append(current)
            raw_prices.append((int_price(match.group("unit_price")), int_price(match.group("supply_price"))))
            pending_desc.clear()
            continue

        if current:
            if re.match(r"^[\d(-]", line):
                current.specification = " ".join(filter(None, [current.specification, line])).strip()
            else:
                pending_desc.append(line)
        else:
            pending_desc.append(line)

    if not items:
        raise ValueError("No purchasable item rows found in quote text")
    normalized, mode = normalize_prices(items, raw_prices, totals)
    return normalized, mode, totals


def safe_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(round(value))
    try:
        return int(str(value).replace(",", "").strip())
    except ValueError:
        return None


def parse_quote_items_from_json(data: dict[str, object]) -> tuple[list[PurchaseItem], str, QuoteTotals]:
    schema = doc_reader.load_schema("purchase")
    validation = common_validators.validate(data, schema)
    if not validation.ok:
        raise ValueError("quote JSON integrity check failed: " + "; ".join(validation.errors))
    raw_items = data.get("items")
    if not isinstance(raw_items, list):
        raise ValueError("quote JSON has no items list")
    items: list[PurchaseItem] = []
    raw_prices: list[tuple[int, int]] = []
    for idx, raw_item in enumerate(raw_items, 1):
        if not isinstance(raw_item, dict):
            continue
        quantity = safe_int(raw_item.get("quantity")) or 1
        unit_raw = safe_int(raw_item.get("unit_price_raw") or raw_item.get("unit_price")) or 0
        amount_raw = safe_int(raw_item.get("amount_raw") or raw_item.get("supply_price") or raw_item.get("total_price")) or unit_raw * quantity
        model = str(raw_item.get("model") or raw_item.get("name") or f"item{idx}").strip()
        name = str(raw_item.get("name") or model).strip()
        items.append(
            PurchaseItem(
                number=safe_int(raw_item.get("number")) or idx,
                name=name,
                specification=str(raw_item.get("specification") or "").strip(),
                model=model,
                quantity=quantity,
                unit_price=0,
                supply_price=0,
            )
        )
        raw_prices.append((unit_raw, amount_raw))
    totals_data = data.get("totals") if isinstance(data.get("totals"), dict) else {}
    totals = QuoteTotals(
        supply_price=safe_int(totals_data.get("supply_price") if isinstance(totals_data, dict) else None),
        vat=safe_int(totals_data.get("vat") if isinstance(totals_data, dict) else None),
        total_price=safe_int(totals_data.get("total_price") if isinstance(totals_data, dict) else None),
    )
    if not items:
        raise ValueError("quote JSON produced no items")
    normalized, mode = normalize_prices(items, raw_prices, totals)
    return normalized, f"llm_{mode}", totals


def parse_quote_items(
    quote_pdf: Path,
    *,
    parse_engine: str,
    ocr_api_url: str,
    ocr_api_key: str,
    litellm_base_url: str,
    litellm_api_key: str,
    litellm_model: str,
    codex_bin: str,
    codex_model: str | None,
    timeout: int,
) -> tuple[list[PurchaseItem], str, QuoteTotals]:
    errors: list[str] = []
    schema = doc_reader.load_schema("purchase")
    if parse_engine in {"auto", "pdf-text"}:
        try:
            items, mode, totals = parse_quote_items_from_text(doc_reader.pdf_text(quote_pdf))
            if parse_engine == "pdf-text" or totals.total_price is not None or totals.supply_price is not None:
                return items, f"pdf_text_{mode}", totals
            errors.append("pdf-text: quote totals not found")
        except Exception as exc:
            if parse_engine == "pdf-text":
                raise
            errors.append(f"pdf-text: {exc}")

    if parse_engine == "ocr-litellm":
        try:
            text = doc_reader.ocr_text(quote_pdf, ocr_api_url, ocr_api_key or litellm_api_key, timeout)
            parsed = doc_reader.litellm_json(text, quote_llm_prompt(), litellm_base_url, litellm_api_key or ocr_api_key, litellm_model, timeout, max_tokens=1800)
            validation = common_validators.validate(parsed, schema)
            if not validation.ok:
                raise ValueError("ocr-litellm integrity check failed: " + "; ".join(validation.errors))
            items, mode, totals = parse_quote_items_from_json(parsed)
            return items, f"ocr_litellm_{mode}", totals
        except Exception:
            raise

    if parse_engine in {"auto", "codex"}:
        try:
            parsed = doc_reader.codex_json(quote_pdf, quote_llm_prompt(), codex_bin, codex_model, timeout)
            validation = common_validators.validate(parsed, schema)
            if not validation.ok:
                raise ValueError("codex integrity check failed: " + "; ".join(validation.errors))
            items, mode, totals = parse_quote_items_from_json(parsed)
            return items, f"codex_{mode}", totals
        except Exception as exc:
            if parse_engine == "codex":
                raise
            errors.append(f"codex: {exc}")

    raise RuntimeError("Could not parse quote automatically:\n" + "\n".join(errors))


def write_items_xls(items: list[PurchaseItem], xls_path: Path) -> None:
    xls_path.parent.mkdir(parents=True, exist_ok=True)
    headers = ["순번", "품명", "규격", "수량", "단가", "공급가액", "부가세액", "총구입액", "용도설명"] + [""] * 18
    workbook = xlwt.Workbook(encoding="utf-8")
    worksheet = workbook.add_sheet("이지바로품목")
    header_style = xlwt.easyxf("font: bold on; align: horiz center")
    index_style = xlwt.easyxf("font: bold on")
    widths = [8, 24, 70, 8, 14, 14, 14, 14, 20] + [8] * 18
    for idx, width in enumerate(widths):
        worksheet.col(idx).width = 256 * width
    for col, value in enumerate(headers):
        worksheet.write(0, col, value, header_style if value else xlwt.Style.default_style)
    for row, item in enumerate(items, 1):
        values = [
            item.number,
            clean_item_name(item),
            clean_item_spec(item),
            item.quantity,
            item.unit_price,
            item.supply_price,
            item.vat,
            item.total_price,
            "",
        ]
        for col, value in enumerate(values):
            worksheet.write(row, col, value, index_style if col == 0 else xlwt.Style.default_style)
    workbook.save(str(xls_path))


def prepare_items_xls(
    *,
    quote_pdf: Path,
    items_path: Path,
    parse_engine: str = "auto",
    ocr_api_url: str = DEFAULT_OCR_API_URL,
    ocr_api_key: str = "",
    litellm_base_url: str = DEFAULT_LITELLM_BASE_URL,
    litellm_api_key: str = "",
    litellm_model: str = "local",
    codex_bin: str = "codex",
    codex_model: str | None = None,
    timeout: int = 180,
) -> tuple[list[PurchaseItem], str, QuoteTotals]:
    items, price_mode, totals = parse_quote_items(
        quote_pdf,
        parse_engine=parse_engine,
        ocr_api_url=ocr_api_url,
        ocr_api_key=ocr_api_key,
        litellm_base_url=litellm_base_url,
        litellm_api_key=litellm_api_key,
        litellm_model=litellm_model,
        codex_bin=codex_bin,
        codex_model=codex_model,
        timeout=timeout,
    )
    write_items_xls(items, items_path)
    return items, price_mode, totals


def clean_item_name(item: PurchaseItem) -> str:
    text = item.model or item.name
    text = re.sub(r"[^0-9A-Za-z가-힣ㄱ-ㅎㅏ-ㅣ.-]", "", str(text))
    return text[:50]


def clean_item_spec(item: PurchaseItem) -> str:
    text = item.specification or item.name
    text = text.replace("Ø", "D").replace("ø", "D")
    text = text.replace("°", "")
    text = re.sub(r"[,\"'/=]", " ", text)
    text = re.sub(r"[^0-9A-Za-z가-힣ㄱ-ㅎㅏ-ㅣ\s.-]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:80]


def natural_key(path: Path) -> list[object]:
    parts = re.split(r"(\d+)", path.stem)
    return [int(part) if part.isdigit() else part.lower() for part in parts]


def image_paths(images_dir: Path) -> list[Path]:
    return sorted(
        [
            path
            for path in images_dir.iterdir()
            if path.is_file() and not path.name.startswith(".") and path.suffix.lower() in IMAGE_EXTENSIONS
        ],
        key=natural_key,
    )


def find_quote_file(purchase_dir: Path) -> Path:
    candidates = [
        path
        for path in purchase_dir.iterdir()
        if path.is_file()
        and not path.name.startswith(".")
        and path.suffix.lower() in QUOTE_EXTENSIONS
        and any(token in path.stem for token in QUOTE_NAME_TOKENS)
    ]
    if candidates:
        return sorted(candidates, key=natural_key)[0]

    pdf_candidates = [
        path
        for path in purchase_dir.iterdir()
        if path.is_file() and not path.name.startswith(".") and path.suffix.lower() == ".pdf"
    ]
    for path in sorted(pdf_candidates, key=natural_key):
        try:
            text = doc_reader.pdf_text(path)
        except Exception:
            continue
        compact = re.sub(r"\s+", "", text).upper()
        if "견적서" in compact or "QUOTATION" in compact:
            return path
    raise FileNotFoundError(f"No quote PDF containing 견적/견적서/QUOTATION found in {purchase_dir}")


def find_images_dir(purchase_dir: Path) -> Path:
    for name in IMAGE_DIR_CANDIDATES:
        path = purchase_dir / name
        if path.is_dir() and image_paths(path):
            return path
    if image_paths(purchase_dir):
        return purchase_dir
    raise FileNotFoundError(f"No image folder with images found. Tried: {', '.join(IMAGE_DIR_CANDIDATES)} in {purchase_dir}")


def draw_fit_image(pdf: canvas.Canvas, image_path: Path, rect: tuple[float, float, float, float]) -> None:
    x1, y1, x2, y2 = rect
    max_w = x2 - x1
    max_h = y2 - y1
    image = ImageOps.exif_transpose(Image.open(image_path)).convert("RGB")
    img_w, img_h = image.size
    scale = min(max_w / img_w, max_h / img_h)
    width = img_w * scale
    height = img_h * scale
    x = x1 + (max_w - width) / 2
    y = y1 + (max_h - height) / 2
    target_px = 300
    resize_scale = min(target_px / img_w, target_px / img_h, 1)
    if resize_scale < 1:
        image = image.resize((int(img_w * resize_scale), int(img_h * resize_scale)), Image.LANCZOS)
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=85, optimize=True)
    buffer.seek(0)
    pdf.drawImage(ImageReader(buffer), x, y, width=width, height=height)


def make_image_overlay(
    page_images: list[Path],
) -> PdfReader:
    packet = BytesIO()
    pdf = canvas.Canvas(packet, pagesize=A4)
    for idx, image_path in enumerate(page_images[:4]):
        draw_fit_image(pdf, image_path, PHOTO_RECTS[idx])
    pdf.save()
    packet.seek(0)
    return PdfReader(packet)


def form_values(page_items: list[PurchaseItem], inspection_date: date, inspector: str) -> dict[str, str]:
    values = {
        "검수년": str(inspection_date.year),
        "검수월": str(inspection_date.month),
        "검수일": str(inspection_date.day),
    }
    if inspector and inspector != "양대호" and inspector.isascii():
        values["검수자"] = inspector
    for idx, item in enumerate(page_items, 1):
        values[f"품명{idx}"] = item.name
    return values


def load_projects_config(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Invalid projects config: {path}")
    return data


def project_defaults(projects_yml: Path, project_id: str) -> dict[str, str]:
    config = load_projects_config(projects_yml)
    defaults = config.get("defaults") or {}
    if not isinstance(defaults, dict):
        raise ValueError(f'"defaults" in {projects_yml} must be a mapping')
    projects = config.get("projects") or {}
    if project_id and isinstance(projects, dict) and project_id not in projects:
        raise ValueError(f"Project {project_id} was not found in {projects_yml}")
    return {str(key): str(value) for key, value in defaults.items() if value is not None}


def set_need_appearances(writer: PdfWriter, value: bool) -> None:
    acroform = writer._root_object.get(NameObject("/AcroForm"))
    if acroform:
        acroform.get_object()[NameObject("/NeedAppearances")] = BooleanObject(value)


def filled_form_reader(form_pdf: Path, values: dict[str, str]) -> PdfReader:
    reader = PdfReader(str(form_pdf))
    writer = PdfWriter()
    writer.clone_document_from_reader(reader)
    writer.update_page_form_field_values(writer.pages[0], values, auto_regenerate=False)
    set_need_appearances(writer, True)
    packet = BytesIO()
    writer.write(packet)
    packet.seek(0)
    return PdfReader(packet)


def attach_acroform_from_page_widgets(writer: PdfWriter) -> None:
    field_refs = ArrayObject()
    font_resource = NameObject("/F2")
    font = DictionaryObject()
    first_page_fonts = writer.pages[0].get("/Resources", {}).get("/Font", DictionaryObject()) if writer.pages else DictionaryObject()
    if font_resource in first_page_fonts:
        font[font_resource] = first_page_fonts[font_resource]
    for page_index, page in enumerate(writer.pages, 1):
        for annot_ref in page.get("/Annots") or []:
            annot = annot_ref.get_object()
            if annot.get("/Subtype") != "/Widget" or not annot.get("/T"):
                continue
            annot[NameObject("/T")] = TextStringObject(f"{annot.get('/T')}_p{page_index}")
            field_refs.append(annot_ref)
    if not field_refs:
        return
    writer._root_object[NameObject("/AcroForm")] = writer._add_object(
        DictionaryObject(
            {
                NameObject("/Fields"): field_refs,
                NameObject("/NeedAppearances"): BooleanObject(True),
                NameObject("/DA"): TextStringObject("/F2 10 Tf 0 g"),
                NameObject("/DR"): DictionaryObject({NameObject("/Font"): font}),
            }
        )
    )


def generate_inspection_pdf(
    form_pdf: Path,
    output_pdf: Path,
    items: list[PurchaseItem],
    images: list[Path],
    inspection_date: date,
    inspector: str,
) -> None:
    if not images:
        raise ValueError("No images found for inspection PDF")
    writer = PdfWriter()
    pages = max(math.ceil(len(items) / 4), math.ceil(len(images) / 4), 1)

    for page_index in range(pages):
        start = page_index * 4
        page_items = items[start : start + 4]
        page_images = images[start : start + 4]
        page_reader = filled_form_reader(form_pdf, form_values(page_items, inspection_date, inspector))
        page = page_reader.pages[0]
        overlay = make_image_overlay(page_images)
        page.merge_page(overlay.pages[0])
        writer.add_page(page)

    attach_acroform_from_page_widgets(writer)
    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    with output_pdf.open("wb") as handle:
        writer.write(handle)


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Parse quote PDF, update items.xls, and generate inspection PDF.")
    parser.add_argument("purchase_dir", nargs="?", type=Path, default=DEFAULT_PURCHASE_DIR)
    parser.add_argument("--quote", help="Quote file name. If omitted, a file containing 견적/견적서 is selected automatically.")
    parser.add_argument("--template", default="물품검수확인서.pdf")
    parser.add_argument("--form-template", type=Path, default=DEFAULT_FORM_TEMPLATE)
    parser.add_argument("--rebuild-form", action="store_true", help="Rebuild --form-template from --template before processing.")
    parser.add_argument("--items", default="items.xls")
    parser.add_argument("--items-only", action="store_true", help="Only parse quote and write items.xls; do not require images or generate inspection PDF.")
    parser.add_argument("--images", help="Image folder name. If omitted, imgs, imgs1, then img are tried.")
    parser.add_argument("--output", default="물품검수확인서_작성.pdf")
    parser.add_argument("--projects-yml", type=Path, default=DEFAULT_PROJECTS_YML)
    parser.add_argument("--project-id", help="Project key/number in projects.yml. Used for shared defaults such as inspector.")
    parser.add_argument("--inspection-date", type=parse_date, default=date.today())
    parser.add_argument("--inspector")
    parser.add_argument("--parse-engine", choices=["auto", "pdf-text", "ocr-litellm", "codex"], default="auto")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--ocr-api-url", default=os.environ.get("DHLAB_OCR_API_URL", DEFAULT_OCR_API_URL))
    parser.add_argument("--ocr-api-key", default=os.environ.get("DHLAB_OCR_API_KEY") or os.environ.get("DHLAB_LITELLM_API_KEY", ""))
    parser.add_argument("--litellm-base-url", default=os.environ.get("DHLAB_LITELLM_BASE_URL", DEFAULT_LITELLM_BASE_URL))
    parser.add_argument("--litellm-api-key", default=os.environ.get("DHLAB_LITELLM_API_KEY", ""))
    parser.add_argument("--litellm-model", default=os.environ.get("DHLAB_LITELLM_MODEL", "local"))
    parser.add_argument("--codex-bin", default="codex")
    parser.add_argument("--codex-model")
    return parser.parse_args()


def resolve_template_path(path: Path) -> Path:
    if path.is_absolute():
        return path
    return Path.cwd() / path


def main() -> None:
    args = parse_args()
    defaults = project_defaults(args.projects_yml, args.project_id or "") if args.projects_yml else {}
    inspector = args.inspector or defaults.get("inspector") or "양대호"
    purchase_dir = args.purchase_dir
    quote_pdf = purchase_dir / args.quote if args.quote else find_quote_file(purchase_dir)
    template_pdf = purchase_dir / args.template
    items_path = purchase_dir / args.items
    images_dir = purchase_dir / args.images if args.images else None
    form_pdf = resolve_template_path(args.form_template)
    output_pdf = purchase_dir / args.output

    if args.rebuild_form or not form_pdf.exists():
        add_inspection_fields(template_pdf, form_pdf, need_appearances=True)
    if not form_pdf.exists():
        raise FileNotFoundError(f"Missing inspection form template: {form_pdf}")
    items, price_mode, totals = prepare_items_xls(
        quote_pdf=quote_pdf,
        items_path=items_path,
        parse_engine=args.parse_engine,
        ocr_api_url=args.ocr_api_url,
        ocr_api_key=args.ocr_api_key,
        litellm_base_url=args.litellm_base_url,
        litellm_api_key=args.litellm_api_key,
        litellm_model=args.litellm_model,
        codex_bin=args.codex_bin,
        codex_model=args.codex_model,
        timeout=args.timeout,
    )
    if args.items_only:
        print(f"items: {items_path}")
        print(f"items_count: {len(items)}")
        print(f"price_mode: {price_mode}")
        print(f"quote_totals: supply={totals.supply_price}, vat={totals.vat}, total={totals.total_price}")
        print(f"normalized_totals: supply={sum(item.supply_price for item in items)}, vat={sum(item.vat for item in items)}, total={sum(item.total_price for item in items)}")
        return

    images_dir = images_dir or find_images_dir(purchase_dir)
    images = image_paths(images_dir)
    generate_inspection_pdf(form_pdf, output_pdf, items, images, args.inspection_date, inspector)

    print(f"items: {items_path}")
    print(f"form: {form_pdf}")
    print(f"inspection_pdf: {output_pdf}")
    print(f"items_count: {len(items)}")
    print(f"images_count: {len(images)}")
    print(f"inspector: {inspector}")
    print(f"price_mode: {price_mode}")
    print(f"quote_totals: supply={totals.supply_price}, vat={totals.vat}, total={totals.total_price}")
    print(f"normalized_totals: supply={sum(item.supply_price for item in items)}, vat={sum(item.vat for item in items)}, total={sum(item.total_price for item in items)}")


if __name__ == "__main__":
    main()
