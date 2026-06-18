from __future__ import annotations

import re
from dataclasses import dataclass

from .vendors import normalize_vendor


DOC_TYPES = (
    "tax_invoice",
    "estimate",
    "statement",
    "business_registration",
    "bankbook_copy",
    "receipt",
)

TAX_INVOICE_DOCUMENTS = (
    "tax_invoice",
    "estimate",
    "statement",
    "business_registration",
    "bankbook_copy",
)

CARD_PAYMENT_DOCUMENTS = (
    "receipt",
    "estimate",
    "statement",
)

REQUIRED_DOCUMENTS = TAX_INVOICE_DOCUMENTS
TAX_READY_DOCUMENTS = ("tax_invoice", "estimate", "statement")

DOC_TYPE_LABELS = {
    "tax_invoice": "전자세금계산서",
    "estimate": "견적서",
    "statement": "거래명세서",
    "business_registration": "사업자등록증",
    "bankbook_copy": "통장사본",
    "receipt": "영수증",
    "unknown": "미분류",
}

KEYWORDS = {
    "tax_invoice": ("전자세금", "전자(세금)", "세금계산서", "nts_etaxinvoice", "taxinvoice", "전세"),
    "estimate": ("견적서", "견적", "quotation", "quote"),
    "statement": ("거래명세서", "거래 명세서", "거명", "statement"),
    "business_registration": ("사업자등록증", "사업자 등록증", "사업자"),
    "bankbook_copy": ("통장사본", "통장 사본", "계좌사본", "계좌 사본", "bankbook", "account copy", "통장"),
    "receipt": ("영수증", "카드전표", "매출전표", "kg이니시스", "receipt"),
}

ADMIN_SENDERS = ("phys@gachon.ac.kr",)

ADDRESS_TOKENS = (
    "서울시",
    "서울특별시",
    "대전시",
    "대전광역시",
    "경기도",
    "인천광역시",
    "부산광역시",
    "대구광역시",
    "광주광역시",
    "울산광역시",
    "세종시",
)


@dataclass(frozen=True)
class Classification:
    doc_type: str
    all_doc_types: tuple[str, ...]
    confidence: float
    reason: str
    document_number: str | None = None
    item_code: str | None = None


def compact_text(*values: str | None) -> str:
    return "\n".join(v for v in values if v).lower()


def required_documents_for_doc_types(doc_types: set[str]) -> tuple[str, ...]:
    if "receipt" in doc_types and "tax_invoice" not in doc_types:
        return CARD_PAYMENT_DOCUMENTS
    return TAX_INVOICE_DOCUMENTS


def missing_documents_for_doc_types(doc_types: set[str]) -> list[str]:
    return [doc_type for doc_type in required_documents_for_doc_types(doc_types) if doc_type not in doc_types]


def is_finished_doc_types(doc_types: set[str]) -> bool:
    return set(TAX_INVOICE_DOCUMENTS).issubset(doc_types) or set(CARD_PAYMENT_DOCUMENTS).issubset(doc_types)


def purchase_status_from_doc_types(doc_types: set[str]) -> str:
    if is_finished_doc_types(doc_types):
        return "finished"
    if set(TAX_READY_DOCUMENTS).issubset(doc_types):
        return "ready"
    return "incomplete"


def document_types_from_text(text: str) -> list[str]:
    lower = text.lower()
    found: list[str] = []
    for doc_type, keywords in KEYWORDS.items():
        if any(keyword.lower() in lower for keyword in keywords):
            found.append(doc_type)
    return found


def document_types_from_filename(filename: str) -> list[str]:
    return document_types_from_text(filename)


def extract_codes(text: str) -> tuple[str | None, str | None]:
    patterns = [
        r"\b\d{5}[A-Z]-[A-Z0-9-]+\b",
        r"\b\d{2}-\d{3,5}[A-Z]*\b",
        r"\b[A-Z]{2,}\d{3,}[A-Z0-9-]*\b",
        r"\b\d{10,}\b",
    ]
    candidates: list[str] = []
    upper = text.upper()
    for pattern in patterns:
        for match in re.findall(pattern, upper):
            if match not in candidates:
                candidates.append(match)
    document_number = candidates[0] if candidates else None
    item_code = next((c for c in candidates if re.search(r"[A-Z]", c)), document_number)
    return document_number, item_code


def classify_document(filename: str, subject: str = "", body_text: str = "", from_: str = "") -> Classification:
    filename_types = document_types_from_filename(filename)
    context_types = document_types_from_text(compact_text(subject, body_text))
    ordered = []
    for doc_type in [*filename_types, *context_types]:
        if doc_type not in ordered:
            ordered.append(doc_type)

    if not ordered:
        return Classification("unknown", tuple(), 0.0, "no keyword match", *extract_codes(f"{filename}\n{subject}"))

    scores: dict[str, float] = {doc_type: 0.0 for doc_type in ordered}
    for doc_type in filename_types:
        scores[doc_type] += 0.55
    for doc_type in context_types:
        scores[doc_type] += 0.10 if doc_type == "tax_invoice" and doc_type not in filename_types else 0.25
    if "statement" in context_types and "statement" in scores and not any(t in filename_types for t in ("tax_invoice", "statement")):
        scores["statement"] += 0.20
    if extract_vendor(subject, body_text, from_):
        for doc_type in scores:
            scores[doc_type] += 0.10
    document_number, item_code = extract_codes(f"{filename}\n{subject}\n{body_text[:2000]}")
    if document_number or item_code:
        for doc_type in scores:
            scores[doc_type] += 0.10

    # If a filename explicitly names multiple docs, keep all of them but choose the first by score.
    best = max(ordered, key=lambda item: (scores[item], -ordered.index(item)))
    confidence = min(1.0, scores[best])
    reason = "filename/context keyword match"
    return Classification(best, tuple(ordered), confidence, reason, document_number, item_code)


def compact_no_space(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


def classify_document_content(filename: str, document_text: str, fallback: Classification | None = None) -> Classification:
    compact = compact_no_space(document_text)
    filename_types = document_types_from_filename(filename)
    found: list[str] = []

    has_actual_tax_invoice = (
        ("전자세금계산서" in compact or "전자(세금)계산서" in compact)
        and "승인번호" in compact
        and "공급가액" in compact
        and "합계금액" in compact
    )
    if has_actual_tax_invoice:
        found.append("tax_invoice")
    if "사업자등록증" in compact:
        found.append("business_registration")
    if "통장사본" in compact or "계좌사본" in compact:
        found.append("bankbook_copy")
    if "거래명세서" in compact or "거래명세표" in compact:
        found.append("statement")
    if "견적서" in compact or "quotation" in document_text.lower() or "견적합니다" in compact:
        found.append("estimate")

    for doc_type in filename_types:
        if doc_type in {"business_registration", "bankbook_copy"} and doc_type not in found:
            found.insert(0, doc_type)

    ordered: list[str] = []
    for doc_type in [*found, *filename_types]:
        if doc_type not in ordered:
            ordered.append(doc_type)

    if not ordered:
        if fallback and fallback.doc_type != "unknown":
            return fallback
        return Classification("unknown", tuple(), 0.0, "no document content match", *extract_codes(filename))

    if "tax_invoice" in ordered:
        primary = "tax_invoice"
    elif "business_registration" in ordered:
        primary = "business_registration"
    elif "bankbook_copy" in ordered:
        primary = "bankbook_copy"
    elif "statement" in ordered:
        primary = "statement"
    else:
        primary = ordered[0]

    document_number, item_code = extract_codes(f"{filename}\n{document_text[:3000]}")
    confidence = 0.95 if primary == "tax_invoice" else 0.85
    return Classification(primary, tuple(ordered), confidence, "pdf content match", document_number, item_code)


def extract_issue_date_from_document_text(document_text: str, fallback: str | None = None) -> str | None:
    patterns = [
        r"작성일자[\s\S]{0,220}?(20\d{2})[./\s년]+(\d{1,2})[./\s월]+(\d{1,2})",
        r"작성[\s\S]{0,220}?(20\d{2})\s+(\d{1,2})\s+(\d{1,2})",
        r"납품일자\s*(20\d{2})[./\s년]+(\d{1,2})[./\s월]+(\d{1,2})",
        r"견적일자\s*(20\d{2})[./\s년]+(\d{1,2})[./\s월]+(\d{1,2})",
        r"일시:\s*(20\d{2})년\s*(\d{1,2})월\s*(\d{1,2})일",
        r"(20\d{2})[-/.년]\s*(\d{1,2})[-/.월]\s*(\d{1,2})",
    ]
    for pattern in patterns:
        match = re.search(pattern, document_text)
        if match:
            year, month, day = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
            return f"{year:04d}-{month:02d}-{day:02d}"
    return fallback


def extract_vendor_from_document_text(document_text: str, doc_type: str | None = None) -> str | None:
    if doc_type == "tax_invoice" or "전자세금계산서" in compact_no_space(document_text):
        for pattern in (
            r"상호\s+(.+?)\s+성명",
            r"\(법인명\)\s*([^\n]+?)\s+(?:명|성명|공\s*\(법인명\))",
        ):
            match = re.search(pattern, document_text)
            if match:
                vendor = clean_vendor(match.group(1))
                if vendor and "가천대학교" not in vendor:
                    return vendor

    for pattern in (
        r"((?:\(주\)|㈜|주식회사)\s*[가-힣A-Za-z0-9 ]+)",
        r"상호\s*[:：]\s*([^ㅣ|\n]+)",
        r"상\s*호\s+([^\n]+)",
        r"회사명/대표\s+([^/\n]+)",
        r"예금주:\s*([^\n)]+)",
    ):
        match = re.search(pattern, document_text)
        if match:
            vendor = clean_vendor(match.group(1))
            if vendor and "가천대학교" not in vendor:
                return vendor
    return None


def extract_vendor(subject: str = "", body_text: str = "", from_: str = "") -> str | None:
    for text in (subject, body_text):
        for pattern in (
            r"\((.+?)->",
            r"\(([^()]+?)->",
            r"\[([^\]]+)\].*전자\(세금\)계산서",
            r"보낸회사\s+(.+?)(?:\n|발행일자)",
            r"이용하여\s+(.+?)\s+사업자가",
            r"((?:\(주\)|㈜|주식회사)\s*[가-힣A-Za-z0-9 ]+)",
            r"([가-힣A-Za-z0-9]+(?:컴퍼니|포토닉스|이노텍|클라우드|엔티렉스|포랩|피씨|마트))",
        ):
            match = re.search(pattern, text, flags=re.DOTALL)
            if match:
                vendor = clean_vendor(match.group(1))
                if vendor:
                    return vendor

    display = from_.split("<", 1)[0].strip().strip('"')
    display = display.split("/", 1)[0].strip()
    if " - " in display:
        display = display.rsplit(" - ", 1)[-1].strip()
    display = clean_vendor(display)
    if display and "국세청" not in display and "gmail" not in display.lower():
        return display
    return None


def clean_vendor(value: str) -> str:
    vendor = " ".join(value.split()).strip(" .,:")
    vendor = re.sub(r"\s+성\s*명.*$", "", vendor).strip()
    for token in ADDRESS_TOKENS:
        vendor = vendor.split(token, 1)[0].strip()
    vendor = re.sub(r"\s+(Mobile|Tel|TEL|전화|담당|Website|E-Mail|김도연|김유곤|조동혁).*$", "", vendor).strip()
    if "포랩" in vendor:
        return "포랩"
    if " X " in vendor:
        vendor = vendor.split(" X ", 1)[0].strip()
    if vendor.endswith("(Optic Cloud)"):
        vendor = vendor.replace("(Optic Cloud)", "").strip()
    return vendor


def is_probably_admin_notice(from_: str) -> bool:
    lower = from_.lower()
    return any(sender in lower for sender in ADMIN_SENDERS)


def vendor_matches(left: str | None, right: str | None) -> bool:
    return bool(normalize_vendor(left) and normalize_vendor(left) == normalize_vendor(right))
