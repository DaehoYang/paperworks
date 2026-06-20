from __future__ import annotations

import sqlite3
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from pypdf import PdfWriter

from scripts.documents.backfill_processed_sources import backfill_from_json
from scripts.documents.db import (
    backfill_processed_sources_from_documents,
    connect,
    load_documents,
    processed_source_has_existing_document,
    processed_source_row,
    record_processed_document,
    replace_local_purchase_documents,
    source_key,
    upsert_document,
    upsert_processed_source,
    upsert_purchase_case,
)
from scripts.documents.place_purchase_docs import (
    PlacementPlan,
    apply_plan,
    build_plans,
    case_status_from_doc_types,
    copy_vendor_docs_to_purchase,
    fill_existing_card_payment_cases,
    refresh_vendor_store_from_purchase,
    vendor_metadata,
)
from scripts.documents.purchase_scan import scan_purchase_root


def write_blank_pdf(path: Path) -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    with path.open("wb") as handle:
        writer.write(handle)


class PurchaseScanDbTests(unittest.TestCase):
    def test_scan_standard_purchase_case(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "purchase"
            case = root / "260618_에이이노텍"
            case.mkdir(parents=True)
            (case / "견적.pdf").write_bytes(b"quote")
            (case / "거명.pdf").write_bytes(b"statement")
            (case / "전세.pdf").write_bytes(b"tax")
            (case / "사업자등록증.pdf").write_bytes(b"biz")
            (case / "하나은행 통장사본.pdf").write_bytes(b"bank")

            cases = scan_purchase_root(root)
            self.assertEqual(len(cases), 1)
            scanned = cases[0]
            self.assertEqual(scanned.case_date, "2026-06-18")
            self.assertEqual(scanned.vendor, "에이이노텍")
            self.assertEqual(set(scanned.local_docs), {
                "estimate",
                "statement",
                "tax_invoice",
                "business_registration",
                "bankbook_copy",
            })

    def test_scan_ignores_vendor_store(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "purchase"
            vendor_dir = root / "vendors" / "에이이노텍"
            vendor_dir.mkdir(parents=True)
            (vendor_dir / "사업자등록증.pdf").write_bytes(b"biz")

            cases = scan_purchase_root(root)
            self.assertEqual(cases, [])

    def test_scan_uses_sidecar_all_doc_types_for_combined_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "purchase"
            case = root / "260608_성경포토닉스"
            case.mkdir(parents=True)
            (case / "26-007-1.pdf").write_bytes(b"combined")
            (case / "26-007-1.json").write_text(
                json.dumps(
                    {
                        "doc_type": "statement",
                        "all_doc_types": [
                            "statement",
                            "estimate",
                            "business_registration",
                            "bankbook_copy",
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (case / "전세.pdf").write_bytes(b"tax")

            cases = scan_purchase_root(root)
            self.assertEqual(len(cases), 1)
            self.assertEqual(
                set(cases[0].local_docs),
                {
                    "tax_invoice",
                    "statement",
                    "estimate",
                    "business_registration",
                    "bankbook_copy",
                },
            )

    def test_refresh_vendor_store_from_purchase(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "purchase"
            case = root / "260618_에이이노텍"
            case.mkdir(parents=True)
            (case / "사업자등록증.pdf").write_bytes(b"biz")
            (case / "통장사본.pdf").write_bytes(b"bank")

            vendor_root = root / "vendors"
            with patch("scripts.documents.place_purchase_docs.validated_vendor_doc_metadata") as validated:
                validated.side_effect = lambda source, doc_type, vendor, base_metadata=None: vendor_metadata(
                    source, doc_type, vendor
                )
                docs = refresh_vendor_store_from_purchase(
                    purchase_root=root,
                    vendor_root=vendor_root,
                    db_path=Path(td) / "documents.sqlite3",
                )

            self.assertEqual({doc["doc_type"] for doc in docs}, {"business_registration", "bankbook_copy"})
            self.assertTrue((vendor_root / "에이이노텍" / "사업자등록증.pdf").exists())
            self.assertTrue((vendor_root / "에이이노텍" / "통장사본.pdf").exists())
            self.assertTrue((vendor_root / "에이이노텍" / "사업자등록증.json").exists())

    def test_refresh_vendor_store_overrides_combined_source_metadata_type(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "purchase"
            case = root / "260608_성경포토닉스"
            case.mkdir(parents=True)
            combined = case / "26-007-1.pdf"
            combined.write_bytes(b"combined")
            combined.with_suffix(".json").write_text(
                json.dumps(
                    {
                        "doc_type": "statement",
                        "all_doc_types": [
                            "statement",
                            "estimate",
                            "business_registration",
                            "bankbook_copy",
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            vendor_root = root / "vendors"
            with patch("scripts.documents.place_purchase_docs.validated_vendor_doc_metadata") as validated:
                validated.side_effect = lambda source, doc_type, vendor, base_metadata=None: vendor_metadata(
                    source, doc_type, vendor
                )
                refresh_vendor_store_from_purchase(
                    purchase_root=root,
                    vendor_root=vendor_root,
                    db_path=Path(td) / "documents.sqlite3",
                )

            biz = json.loads((vendor_root / "성경포토닉스" / "사업자등록증.json").read_text(encoding="utf-8"))
            bank = json.loads((vendor_root / "성경포토닉스" / "통장사본.json").read_text(encoding="utf-8"))
            self.assertEqual(biz["doc_type"], "business_registration")
            self.assertEqual(biz["all_doc_types"], ["business_registration"])
            self.assertEqual(bank["doc_type"], "bankbook_copy")
            self.assertEqual(bank["all_doc_types"], ["bankbook_copy"])

    def test_copy_vendor_docs_to_purchase_only_when_doc_type_missing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "purchase"
            case = root / "260618_에이이노텍"
            case.mkdir(parents=True)
            (case / "하나은행 통장사본.pdf").write_bytes(b"local-bank")

            vendor = root / "vendors" / "에이이노텍"
            vendor.mkdir(parents=True)
            for doc_type, filename in {
                "business_registration": "사업자등록증.pdf",
                "bankbook_copy": "통장사본.pdf",
            }.items():
                pdf = vendor / filename
                pdf.write_bytes(doc_type.encode())
                pdf.with_suffix(".json").write_text(
                    json.dumps(
                        {
                            "doc_type": doc_type,
                            "all_doc_types": [doc_type],
                            "vendor": "에이이노텍",
                            "normalized_vendor": "에이이노텍",
                            "file_path": str(pdf),
                            "saved_pdf": str(pdf),
                            "json_path": str(pdf.with_suffix(".json")),
                            "sha256": doc_type,
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )

            with patch("scripts.documents.place_purchase_docs.validated_vendor_doc_metadata") as validated:
                validated.side_effect = lambda source, doc_type, vendor, base_metadata=None: dict(base_metadata or {})
                copied = copy_vendor_docs_to_purchase(purchase_root=root, vendor_root=root / "vendors")

            self.assertEqual(copied, [case / "사업자등록증.pdf"])
            self.assertTrue((case / "사업자등록증.pdf").exists())
            self.assertFalse((case / "통장사본.pdf").exists())
            self.assertTrue((case / "하나은행 통장사본.pdf").exists())

    def test_fill_existing_card_payment_case_from_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "purchase"
            case = root / "260506_엔티렉스"
            case.mkdir(parents=True)
            (case / "KG이니시스 온라인 영수증.pdf").write_bytes(b"receipt")

            source = Path(td) / "incoming"
            source.mkdir()
            docs = []
            for doc_type, filename in {"estimate": "quote.pdf", "statement": "statement.pdf"}.items():
                pdf = source / filename
                pdf.write_bytes(doc_type.encode())
                metadata = {
                    "doc_type": doc_type,
                    "all_doc_types": [doc_type],
                    "vendor": "엔티렉스",
                    "normalized_vendor": "엔티렉스",
                    "issue_date": "2026-05-06",
                    "file_path": str(pdf),
                    "saved_pdf": str(pdf),
                    "json_path": str(pdf.with_suffix(".json")),
                    "sha256": f"{doc_type}-sha",
                    "confidence": 0.9,
                }
                pdf.with_suffix(".json").write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
                docs.append(metadata)

            placed = fill_existing_card_payment_cases(
                docs=docs,
                purchase_root=root,
                db_path=Path(td) / "documents.sqlite3",
                min_score=0.20,
            )

            self.assertEqual(placed, [case / "견적.pdf", case / "거명.pdf"])
            self.assertTrue((case / "견적.pdf").exists())
            self.assertTrue((case / "거명.pdf").exists())
            conn = sqlite3.connect(Path(td) / "documents.sqlite3")
            status = conn.execute("SELECT status FROM purchase_cases WHERE case_name=?", (case.name,)).fetchone()[0]
            conn.close()
            self.assertEqual(status, "finished")

    def test_card_payment_fill_does_not_create_case_without_receipt_folder(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "purchase"
            root.mkdir()
            source = Path(td) / "incoming"
            source.mkdir()
            pdf = source / "quote.pdf"
            pdf.write_bytes(b"estimate")
            docs = [
                {
                    "doc_type": "estimate",
                    "all_doc_types": ["estimate"],
                    "vendor": "엔티렉스",
                    "normalized_vendor": "엔티렉스",
                    "issue_date": "2026-05-06",
                    "file_path": str(pdf),
                    "saved_pdf": str(pdf),
                    "json_path": str(pdf.with_suffix(".json")),
                    "sha256": "estimate-sha",
                }
            ]

            placed = fill_existing_card_payment_cases(docs=docs, purchase_root=root, db_path=None, min_score=0.20)

            self.assertEqual(placed, [])
            self.assertEqual(list(root.iterdir()), [])

    def test_apply_plan_places_pdf_and_json_in_purchase_case(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "incoming"
            source.mkdir()
            docs = {}
            for doc_type, filename in {
                "tax_invoice": "tax.pdf",
                "estimate": "estimate.pdf",
                "statement": "statement.pdf",
            }.items():
                pdf_path = source / filename
                json_path = pdf_path.with_suffix(".json")
                pdf_path.write_bytes(doc_type.encode())
                metadata = {
                    "doc_type": doc_type,
                    "all_doc_types": [doc_type],
                    "vendor": "에이이노텍",
                    "normalized_vendor": "에이이노텍",
                    "issue_date": "2026-06-18",
                    "amount": 1000,
                    "source": "gmail",
                    "source_filename": filename,
                    "saved_pdf": str(pdf_path),
                    "file_path": str(pdf_path),
                    "json_path": str(json_path),
                    "sha256": doc_type,
                    "confidence": 0.9,
                }
                json_path.write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
                docs[doc_type] = metadata

            target = Path(td) / "purchase" / "260618_에이이노텍"
            plan = PlacementPlan("ready", docs["tax_invoice"], target, docs, "ready")
            placed = apply_plan(plan, Path(td) / "purchase" / "documents.sqlite3", move_sources=True)

            self.assertEqual(placed, target)
            self.assertTrue((target / "전세.pdf").exists())
            self.assertTrue((target / "전세.json").exists())
            self.assertFalse((source / "tax.pdf").exists())
            metadata = json.loads((target / "전세.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["file_path"], str(target / "전세.pdf"))
            self.assertEqual(metadata["json_path"], str(target / "전세.json"))

    def test_tax_only_plan_creates_incomplete_purchase_case(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "incoming"
            source.mkdir()
            pdf_path = source / "tax.pdf"
            json_path = source / "tax.json"
            pdf_path.write_bytes(b"tax")
            tax_doc = {
                "doc_type": "tax_invoice",
                "all_doc_types": ["tax_invoice"],
                "vendor": "메타컴퍼니",
                "normalized_vendor": "메타컴퍼니",
                "issue_date": "2026-06-08",
                "amount": 1000,
                "source": "gmail",
                "source_filename": "NTS_eTaxInvoice.html",
                "saved_pdf": str(pdf_path),
                "file_path": str(pdf_path),
                "json_path": str(json_path),
                "sha256": "tax-only",
                "confidence": 0.9,
            }
            json_path.write_text(json.dumps(tax_doc, ensure_ascii=False), encoding="utf-8")

            purchase_root = Path(td) / "purchase"
            plans = build_plans(
                docs=[tax_doc],
                purchase_root=purchase_root,
                min_score=0.50,
                include_vendor_docs=True,
            )
            self.assertEqual(len(plans), 1)
            self.assertEqual(plans[0].status, "incomplete")

            placed = apply_plan(plans[0], Path(td) / "purchase" / "documents.sqlite3", move_sources=True)
            self.assertEqual(placed, purchase_root / "260608_메타컴퍼니")
            self.assertTrue((placed / "전세.pdf").exists())
            conn = sqlite3.connect(Path(td) / "purchase" / "documents.sqlite3")
            status = conn.execute("SELECT status FROM purchase_cases WHERE case_dir=?", (str(placed),)).fetchone()[0]
            conn.close()
            self.assertEqual(status, "incomplete")

    def test_existing_incomplete_case_is_filled_without_replacing_tax(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            purchase_root = Path(td) / "purchase"
            case = purchase_root / "260608_메타컴퍼니"
            case.mkdir(parents=True)
            write_blank_pdf(case / "전세.pdf")

            source = Path(td) / "incoming"
            source.mkdir()
            docs = {
                "tax_invoice": {
                    "doc_type": "tax_invoice",
                    "vendor": "메타컴퍼니",
                    "normalized_vendor": "메타컴퍼니",
                    "issue_date": "2026-06-08",
                    "amount": 1000,
                    "item_prices": [900],
                    "file_path": str(source / "tax.pdf"),
                    "saved_pdf": str(source / "tax.pdf"),
                    "json_path": str(source / "tax.json"),
                    "sha256": "tax-new",
                },
                "estimate": {
                    "doc_type": "estimate",
                    "vendor": "메타컴퍼니",
                    "normalized_vendor": "메타컴퍼니",
                    "issue_date": "2026-06-08",
                    "amount": 1000,
                    "item_prices": [900],
                    "file_path": str(source / "estimate.pdf"),
                    "saved_pdf": str(source / "estimate.pdf"),
                    "json_path": str(source / "estimate.json"),
                    "sha256": "estimate-new",
                },
                "statement": {
                    "doc_type": "statement",
                    "vendor": "메타컴퍼니",
                    "normalized_vendor": "메타컴퍼니",
                    "issue_date": "2026-06-08",
                    "amount": 1000,
                    "item_prices": [900],
                    "file_path": str(source / "statement.pdf"),
                    "saved_pdf": str(source / "statement.pdf"),
                    "json_path": str(source / "statement.json"),
                    "sha256": "statement-new",
                },
            }
            for doc in docs.values():
                Path(doc["file_path"]).write_bytes(doc["doc_type"].encode())
                Path(doc["json_path"]).write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")

            plans = build_plans(
                docs=list(docs.values()),
                purchase_root=purchase_root,
                min_score=0.50,
                include_vendor_docs=False,
            )
            self.assertEqual(plans[0].target_dir, case)
            self.assertEqual(plans[0].status, "ready")
            apply_plan(plans[0], None, move_sources=True)

            self.assertTrue((case / "전세.pdf").exists())
            self.assertTrue((case / "견적.pdf").exists())
            self.assertTrue((case / "거명.pdf").exists())

    def test_case_status_finished_requires_all_required_docs(self) -> None:
        self.assertEqual(case_status_from_doc_types({"tax_invoice"}), "incomplete")
        self.assertEqual(case_status_from_doc_types({"tax_invoice", "estimate", "statement"}), "ready")
        self.assertEqual(
            case_status_from_doc_types(
                {"tax_invoice", "estimate", "statement", "business_registration", "bankbook_copy"}
            ),
            "finished",
        )

    def test_case_status_finished_accepts_card_payment_docs(self) -> None:
        self.assertEqual(case_status_from_doc_types({"receipt"}), "incomplete")
        self.assertEqual(case_status_from_doc_types({"receipt", "estimate"}), "incomplete")
        self.assertEqual(case_status_from_doc_types({"receipt", "estimate", "statement"}), "finished")

    def test_sqlite_document_and_case_upsert(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "documents.sqlite3"
            conn = connect(db_path)
            document_id = upsert_document(
                conn,
                {
                    "doc_type": "estimate",
                    "all_doc_types": ["estimate"],
                    "vendor": "에이이노텍",
                    "normalized_vendor": "에이이노텍",
                    "issue_date": "2026-06-18",
                    "message_id": "m1",
                    "thread_id": "t1",
                    "from": "sender@example.com",
                    "subject": "견적서",
                    "email_date": "2026-06-18T00:00:00+09:00",
                    "gmail_url": "https://mail.google.com/mail/#all/m1",
                    "source_type": "attachment",
                    "saved_pdf": str(Path(td) / "견적.pdf"),
                    "json_path": str(Path(td) / "견적.json"),
                    "sha256": "abc123",
                    "confidence": 0.9,
                },
            )
            self.assertIsInstance(document_id, int)
            self.assertEqual(len(load_documents(conn)), 1)

            case_id = upsert_purchase_case(
                conn,
                {
                    "case_dir": str(Path(td) / "purchase/260618_에이이노텍"),
                    "case_name": "260618_에이이노텍",
                    "case_date": "2026-06-18",
                    "vendor": "에이이노텍",
                    "normalized_vendor": "에이이노텍",
                },
            )
            replace_local_purchase_documents(conn, case_id, {"estimate": [Path(td) / "견적.pdf"]})
            rows = conn.execute("SELECT doc_type, status FROM purchase_documents").fetchall()
            self.assertEqual([(row["doc_type"], row["status"]) for row in rows], [("estimate", "local")])
            conn.close()

    def test_processed_source_uses_message_not_thread_as_skip_key(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "documents.sqlite3"
            pdf_path = Path(td) / "전세.pdf"
            json_path = Path(td) / "전세.json"
            pdf_path.write_bytes(b"tax")
            conn = connect(db_path)
            metadata = {
                "doc_type": "tax_invoice",
                "all_doc_types": ["tax_invoice"],
                "vendor": "메타컴퍼니",
                "normalized_vendor": "메타컴퍼니",
                "issue_date": "2026-06-08",
                "message_id": "message-old",
                "thread_id": "thread-1",
                "from": "sender@example.com",
                "subject": "전자세금계산서",
                "email_date": "2026-06-08T00:00:00+09:00",
                "source": "gmail",
                "source_type": "attachment",
                "source_attachment_id": "att-1",
                "source_filename": "NTS_eTaxInvoice.html",
                "source_size": 123,
                "saved_pdf": str(pdf_path),
                "file_path": str(pdf_path),
                "json_path": str(json_path),
                "sha256": "tax-sha",
                "confidence": 0.9,
            }
            document_id = upsert_document(conn, metadata)
            upsert_processed_source(conn, metadata, document_id=document_id)

            old_key = source_key(
                gmail_message_id="message-old",
                source_type="attachment",
                source_attachment_id="att-1",
                source_filename="NTS_eTaxInvoice.html",
                source_size=123,
            )
            old_row = processed_source_row(conn, source_key_value=old_key)
            self.assertTrue(processed_source_has_existing_document(old_row))

            new_reply_key = source_key(
                gmail_message_id="message-new",
                source_type="attachment",
                source_attachment_id="att-1",
                source_filename="NTS_eTaxInvoice.html",
                source_size=123,
            )
            self.assertIsNone(processed_source_row(conn, source_key_value=new_reply_key))
            conn.close()

    def test_collect_records_processed_source_immediately(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "documents.sqlite3"
            pdf_path = Path(td) / "견적.pdf"
            json_path = Path(td) / "견적.json"
            pdf_path.write_bytes(b"quote")
            metadata = {
                "doc_type": "estimate",
                "all_doc_types": ["estimate"],
                "vendor": "성경포토닉스",
                "normalized_vendor": "성경포토닉스",
                "issue_date": "2026-04-01",
                "amount": 2956800,
                "message_id": "message-quote",
                "thread_id": "thread-quote",
                "from": "sender@example.com",
                "subject": "견적서",
                "email_date": "2026-04-01T00:00:00+09:00",
                "source": "gmail",
                "source_type": "attachment",
                "source_attachment_id": "att-quote",
                "source_filename": "견적.pdf",
                "source_size": 1234,
                "saved_pdf": str(pdf_path),
                "file_path": str(pdf_path),
                "json_path": str(json_path),
                "sha256": "quote-sha",
                "confidence": 0.9,
            }
            conn = connect(db_path)
            try:
                record_processed_document(None, conn, metadata)
                key = source_key(
                    gmail_message_id="message-quote",
                    source_type="attachment",
                    source_attachment_id="att-quote",
                    source_filename="견적.pdf",
                    source_size=1234,
                )
                row = processed_source_row(conn, source_key_value=key)
                self.assertTrue(processed_source_has_existing_document(row))
                docs = load_documents(conn)
                self.assertEqual(len(docs), 1)
                self.assertEqual(docs[0]["doc_type"], "estimate")
            finally:
                conn.close()

    def test_backfill_processed_sources_from_documents(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "documents.sqlite3"
            pdf_path = Path(td) / "견적.pdf"
            json_path = Path(td) / "견적.json"
            pdf_path.write_bytes(b"estimate")
            conn = connect(db_path)
            upsert_document(
                conn,
                {
                    "doc_type": "estimate",
                    "all_doc_types": ["estimate"],
                    "vendor": "에이이노텍",
                    "normalized_vendor": "에이이노텍",
                    "issue_date": "2026-06-18",
                    "message_id": "m-backfill",
                    "thread_id": "t-backfill",
                    "source": "gmail",
                    "source_type": "attachment",
                    "source_filename": "견적.pdf",
                    "source_size": 8,
                    "saved_pdf": str(pdf_path),
                    "file_path": str(pdf_path),
                    "json_path": str(json_path),
                    "sha256": "estimate-sha",
                    "confidence": 0.9,
                },
            )

            self.assertEqual(backfill_processed_sources_from_documents(conn), 1)
            row = processed_source_row(
                conn,
                gmail_message_id="m-backfill",
                source_type="attachment",
                source_filename="견적.pdf",
                source_size=8,
            )
            self.assertTrue(processed_source_has_existing_document(row))
            conn.close()

    def test_backfill_json_prefers_sidecar_pdf_over_same_sha_elsewhere(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "purchase"
            case = root / "260608_성경포토닉스"
            vendor = root / "vendors" / "성경포토닉스"
            case.mkdir(parents=True)
            vendor.mkdir(parents=True)

            original_pdf = case / "combined.pdf"
            vendor_pdf = vendor / "사업자등록증.pdf"
            original_pdf.write_bytes(b"same-pdf")
            vendor_pdf.write_bytes(b"same-pdf")
            sha = "673953e0ad7fc56cb62228a8203046a9e592730acb194ca3216f93053543e266"

            original_pdf.with_suffix(".json").write_text(
                json.dumps(
                    {
                        "doc_type": "statement",
                        "all_doc_types": ["statement", "estimate", "business_registration"],
                        "vendor": "성경포토닉스",
                        "normalized_vendor": "성경포토닉스",
                        "file_path": str(original_pdf),
                        "saved_pdf": str(original_pdf),
                        "json_path": str(original_pdf.with_suffix(".json")),
                        "sha256": sha,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            vendor_pdf.with_suffix(".json").write_text(
                json.dumps(
                    {
                        "doc_type": "business_registration",
                        "all_doc_types": ["business_registration"],
                        "vendor": "성경포토닉스",
                        "normalized_vendor": "성경포토닉스",
                        "file_path": str(vendor_pdf),
                        "saved_pdf": str(vendor_pdf),
                        "json_path": str(vendor_pdf.with_suffix(".json")),
                        "sha256": sha,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            db_path = Path(td) / "documents.sqlite3"
            backfill_from_json([root], db_path, purchase_root=root, include_unplaced=False)

            conn = connect(db_path)
            rows = {
                row["doc_type"]: row["file_path"]
                for row in conn.execute("SELECT doc_type, file_path FROM documents ORDER BY doc_type")
            }
            conn.close()
            self.assertEqual(rows["statement"], str(original_pdf))
            self.assertEqual(rows["business_registration"], str(vendor_pdf))


if __name__ == "__main__":
    unittest.main()
