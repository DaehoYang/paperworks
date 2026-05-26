from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class Member:
    department: str
    position: str
    name: str


@dataclass(frozen=True)
class ReceiptRecord:
    file_name: str
    receipt_path: Path
    generated: datetime
    total_price: int
    store_name: str = ""
    address: str = ""
    receipt_type: str = "unknown"
    transport_type: str = ""
    origin: str = ""
    destination: str = ""
    item_count: int | None = None
    food_count: int | None = None
    drink_count: int | None = None
    ocr_engine: str = "manual"
    ocr_text_path: str = ""
    ocr_result_json: str = "{}"
    status: str = "parsed"
    pair_id: str = ""
    document_type: str = ""
    output_pdf: str = ""
    error: str = ""

    @property
    def stem(self) -> str:
        return self.receipt_path.stem
