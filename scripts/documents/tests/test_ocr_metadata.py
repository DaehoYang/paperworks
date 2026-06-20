import unittest
from pathlib import Path

from scripts.documents.collect_documents import (
    PageAnalysis,
    build_page_segments,
    document_start_types,
    has_tax_invoice_text_signals,
    validate_collected_document,
)
from scripts.documents.ocr_metadata import merge_structured_fields


class OcrMetadataTests(unittest.TestCase):
    def test_merges_vendor_document_fields(self) -> None:
        metadata = {}

        merge_structured_fields(
            metadata,
            {
                "bank_name": "신한은행",
                "account_holder": "(주)피에스케이테크놀로지",
                "account_number": "140-011-035130",
                "business_registration_number": "526-81-00082",
            },
            overwrite=False,
        )

        self.assertEqual(metadata["bank_name"], "신한은행")
        self.assertEqual(metadata["account_holder"], "(주)피에스케이테크놀로지")
        self.assertEqual(metadata["account_number"], "140-011-035130")
        self.assertEqual(metadata["business_registration_number"], "526-81-00082")


class PageSegmentTests(unittest.TestCase):
    def test_merges_consecutive_same_doc_type_pages(self) -> None:
        pages = [
            PageAnalysis(1, Path("p1.pdf"), "견적서 합계금액 1,000", ["estimate"], ["estimate"], "estimate"),
            PageAnalysis(2, Path("p2.pdf"), "품명 수량 단가 금액", ["estimate"], ["estimate"], "estimate"),
            PageAnalysis(3, Path("p3.pdf"), "거래명세서 합계금액 1,000", ["statement"], ["statement"], "statement"),
        ]

        segments = build_page_segments(pages)

        self.assertEqual([(s.doc_type, [p.page_number for p in s.pages]) for s in segments], [
            ("estimate", [1, 2]),
            ("statement", [3]),
        ])

    def test_continuation_page_only_extends_purchase_docs(self) -> None:
        pages = [
            PageAnalysis(1, Path("p1.pdf"), "견적서 합계금액 1,000", ["estimate"], ["estimate"], "estimate"),
            PageAnalysis(2, Path("p2.pdf"), "품명 수량 단가 금액 2,000", [], ["estimate"]),
            PageAnalysis(3, Path("p3.pdf"), "사업자등록증 등록번호", ["business_registration"], ["business_registration"], "business_registration"),
        ]

        segments = build_page_segments(pages)

        self.assertEqual([(s.doc_type, [p.page_number for p in s.pages]) for s in segments], [
            ("estimate", [1, 2]),
            ("business_registration", [3]),
        ])

    def test_tax_invoice_text_signals_allow_parenthesized_and_modified_forms(self) -> None:
        self.assertTrue(has_tax_invoice_text_signals("전자(세금)계산서\n합계금액 1,100"))
        self.assertTrue(has_tax_invoice_text_signals("전자수정세금계산서\n합계금액 1,100"))

    def test_tax_invoice_validation_requires_text_signals(self) -> None:
        metadata = {
            "doc_type": "tax_invoice",
            "vendor": "피에스케이테크놀로지",
            "issue_date": "2026-03-24",
            "amount": 2992000,
        }

        ok, missing, invalid, _ = validate_collected_document(
            "tax_invoice",
            metadata,
            "전자(세금)계산서 승인번호 공급가액 합계금액 2,992,000",
        )
        self.assertTrue(ok)
        self.assertEqual(missing, [])
        self.assertEqual(invalid, [])

        ok, _, invalid, _ = validate_collected_document("tax_invoice", metadata, "거래명세서 합계금액 2,992,000")
        self.assertFalse(ok)
        self.assertIn("tax_invoice_text", invalid)

    def test_document_start_types_detects_combined_tax_statement_text(self) -> None:
        starts = document_start_types("전자(세금)계산서 합계금액 1,000\n거래명세서")

        self.assertEqual(starts, {"tax_invoice", "statement"})


if __name__ == "__main__":
    unittest.main()
