from __future__ import annotations

import unittest

from scripts.documents.classifiers import (
    classify_document,
    classify_document_content,
    document_types_from_filename,
    extract_codes,
    extract_issue_date_from_document_text,
    extract_vendor,
    extract_vendor_from_document_text,
)
from scripts.documents.vendors import normalize_vendor, parse_case_name
from pathlib import Path


class ClassifierTests(unittest.TestCase):
    def test_filename_can_match_multiple_doc_types(self) -> None:
        self.assertEqual(
            document_types_from_filename("2026060315020817644 거래명세서, 견적서.pdf"),
            ["estimate", "statement"],
        )

    def test_classifies_reusable_vendor_docs(self) -> None:
        self.assertEqual(classify_document("사업자등록증.pdf").doc_type, "business_registration")
        self.assertEqual(classify_document("하나은행 통장사본.pdf").doc_type, "bankbook_copy")

    def test_statement_attachment_wins_over_tax_subject_context(self) -> None:
        result = classify_document(
            "가천대-260617(36243C-P3).pdf",
            subject="[가천대학교] 세금계산서 발행건- ㈜에이이노텍",
            body_text="첨부드린 거래명세서 참고하여 확인 부탁드립니다.",
        )
        self.assertEqual(result.doc_type, "statement")

    def test_extracts_document_codes(self) -> None:
        document_number, item_code = extract_codes("가천대-260617(36243C-P3).pdf")
        self.assertEqual(document_number, "36243C-P3")
        self.assertEqual(item_code, "36243C-P3")

    def test_vendor_normalization(self) -> None:
        self.assertEqual(normalize_vendor("주식회사 에이이노텍"), "에이이노텍")
        self.assertEqual(normalize_vendor("(주)성경포토닉스"), "성경포토닉스")

    def test_extract_vendor_nested_parentheses(self) -> None:
        self.assertEqual(
            extract_vendor("가천대학교산학협력단 ((주) 엔티렉스->가천대학교산학협력단)"),
            "(주) 엔티렉스",
        )

    def test_extract_vendor_trims_signature_address(self) -> None:
        self.assertEqual(
            extract_vendor(body_text="(주) 성경포토닉스 대전시 유성구 지족로 355"),
            "(주) 성경포토닉스",
        )
        self.assertEqual(
            extract_vendor(from_='"정희라 사원 (May Jung) - 옵틱 클라우드 (Optic Cloud)" <may@opticcloud.co.kr>'),
            "옵틱 클라우드",
        )

    def test_pdf_content_overrides_email_tax_context(self) -> None:
        fallback = classify_document(
            "26-007-3 양대호교수님(#26-1617Y).pdf",
            subject="(주)성경포토닉스- 결제 3건 전자세금계산서",
        )
        text = """
        거 래 명 세 서
        2026년 6월 9일 등록번호: 204-81-47440
        상호: ㈜아이넥서스 ㅣ 성명: 정미영
        QUOTATION
        TOTAL AMOUNT 1,961,300
        """
        result = classify_document_content("26-007-3 양대호교수님(#26-1617Y).pdf", text, fallback)
        self.assertEqual(result.doc_type, "statement")
        self.assertIn("estimate", result.all_doc_types)
        self.assertEqual(extract_vendor_from_document_text(text, result.doc_type), "㈜아이넥서스")

    def test_extracts_tax_invoice_supplier_and_issue_date(self) -> None:
        text = """
        전자세금계산서
        상호 (주) 엔티렉스          성명 오상혁
        작성일자                공급가액                     세액
        2026/06/09                   571,600                57,160
        합계금액 628,760
        """
        self.assertEqual(classify_document_content("NTS_eTaxInvoice.html", text).doc_type, "tax_invoice")
        self.assertEqual(extract_vendor_from_document_text(text, "tax_invoice"), "(주) 엔티렉스")
        self.assertEqual(extract_issue_date_from_document_text(text), "2026-06-09")

    def test_business_registration_tax_email_phrase_is_not_tax_invoice(self) -> None:
        text = """
        사 업 자 등 록 증
        법 인 명 ( 단 체 명 ) : 주식회사에이이노텍
        전자세금계산서 전용 전자우편주소 : korea@ainnotech.com
        2025 년 08 월 20 일
        """
        self.assertEqual(classify_document_content("사업자등록증.pdf", text).doc_type, "business_registration")

    def test_tax_invoice_date_does_not_use_bank_account_number(self) -> None:
        text = """
        전자세금계산서 승인번호 2026060841000061
        작성 년 월 일 공란
        2026 06 08 공급가액 2,726,000 세액 272,600
        합계금액 2,998,600
        입금계좌: 국민은행 468437-04-010702
        """
        self.assertEqual(extract_issue_date_from_document_text(text), "2026-06-08")

    def test_standard_case_name(self) -> None:
        parsed = parse_case_name(Path("purchase/260618_에이이노텍"))
        self.assertEqual(parsed.case_date, "2026-06-18")
        self.assertEqual(parsed.vendor, "에이이노텍")
        self.assertFalse(parsed.legacy)

    def test_legacy_case_name(self) -> None:
        parsed = parse_case_name(Path("purchase/260618_pmmfa"))
        self.assertEqual(parsed.case_date, "2026-06-18")
        self.assertIsNone(parsed.vendor)
        self.assertTrue(parsed.legacy)


if __name__ == "__main__":
    unittest.main()
