from __future__ import annotations

import unittest

from scripts.documents.amounts import extract_financial_fields, ordered_price_similarity


class AmountExtractionTests(unittest.TestCase):
    def test_extracts_tax_invoice_total_and_item_prices(self) -> None:
        text = """
        작성일자               공급가액                세액
          2026/06/17                 1,455,000                  145,500
        월 일                   품목           규격       수량        단가             공급가액            세액
        06      17         WPH05M-808                    2     727,500         1,455,000       145,500
        합계금액
          1,600,500
        """
        fields = extract_financial_fields(text)
        self.assertEqual(fields.amount, 1600500)
        self.assertEqual(fields.item_count, 1)
        self.assertEqual(fields.item_prices, (1455000,))

    def test_extracts_statement_total_and_line_prices(self) -> None:
        text = """
        공급가액                                ₩1,455,000
        부가세액                                  ₩145,500
        합계금액                                ₩1,600,500
        번호      모델(규격)                품명            수량             단 가                 공급가액                    부가세
        1     WPH05M-808    Half-Wave Plate        2            ₩727,500           ₩1,455,000                ₩145,500
        """
        fields = extract_financial_fields(text)
        self.assertEqual(fields.amount, 1600500)
        self.assertEqual(fields.item_count, 1)
        self.assertEqual(fields.item_prices, (1455000,))

    def test_ordered_price_similarity(self) -> None:
        self.assertEqual(ordered_price_similarity([100, 200], [100, 200]), 1.0)
        self.assertEqual(ordered_price_similarity([100, 200], [200, 100]), 0.0)
        self.assertEqual(ordered_price_similarity([100, 200], [100]), 0.5)


if __name__ == "__main__":
    unittest.main()
