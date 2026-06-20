# 구매 서류 수집/누락 체크

Gmail에서 구매 처리에 필요한 문서를 찾아 PDF와 JSON 메타로 저장하고, `purchase/<YYMMDD>_<업체명>/` 폴더에 필요한 서류가 있는지 확인하는 도구다.

대상 문서:

- 전자세금계산서
- 견적서
- 거래명세서
- 사업자등록증
- 통장사본

## 설치

```bash
python3 -m pip install --user -r scripts/documents/requirements.txt
python3 -m playwright install chromium
```

Google Cloud에서 OAuth **Desktop app** 클라이언트를 만들고 JSON을 내려받아 저장소 루트에 둔다.

```text
credentials.json
```

처음 실행하면 브라우저에서 Gmail 권한 승인을 진행하고 `scripts/documents/token.json`이 생성된다.

## 매일 실행할 단일 명령어

아침에 한 번 돌릴 기본 명령어는 아래 하나다.

```bash
HOMETAX_PASSWORD=1234567890 python3 scripts/documents/run_daily.py
```

기본 검색 범위는 최근 90일이다. 견적서/거래명세서와 전자세금계산서가 몇 주에서 두 달 정도 떨어져 도착해도 같은 실행에서 다시 매칭하기 위해 넓게 잡는다.

```bash
HOMETAX_PASSWORD=1234567890 python3 scripts/documents/run_daily.py --newer-than 2m
```

동작 순서:

1. Gmail에서 전자세금계산서, 견적서, 거래명세서, 사업자등록증, 통장사본 후보를 검색한다.
2. 기존 `purchase/` 안에서 사업자등록증/통장사본을 찾아 `purchase/vendors/<업체명>/`에 등록한다.
3. 후보 첨부/링크가 이미 `processed_sources`에 있고 저장된 PDF가 존재하면 다운로드와 변환을 건너뛴다.
4. 새 후보는 `purchase/.incoming/`에 PDF와 JSON 메타로 저장하고 문서 종류, 업체명, 날짜, 금액을 분류한다.
5. Gmail에서 새로 받은 사업자등록증/통장사본은 `purchase/vendors/<업체명>/`에 등록한다.
6. `purchase/vendors/<업체명>/`에 있는 사업자등록증/통장사본은 같은 업체 구매 폴더에 해당 문서 타입이 없을 때만 복사한다.
7. 이미 카드영수증이 들어 있는 기존 구매 폴더가 있으면, 새로 발견한 견적서/거래명세서를 날짜와 업체명 기준으로 보강한다.
8. 전자세금계산서를 기준으로 견적서/거래명세서를 금액, 항목 수, 항목별 금액 순서로 매칭한다.
9. 전자세금계산서가 있는 새 구매 건이면 `purchase/<YYMMDD>_<업체명>/` 폴더를 만들고, 현재 확보한 PDF와 JSON 메타를 배치한다.
10. 기존 incomplete 폴더가 있으면 새로 발견한 견적서/거래명세서/업체 문서를 같은 폴더에 보강한다.
11. 완료 기준을 만족하면 케이스를 `finished`로 보고 관련 Gmail 메시지 라벨을 `TaxInvoice/finished`로 승격한다.
12. DB는 `purchase/documents.sqlite3`에 갱신한다.
13. 아직 구매 폴더에 배치되지 않은 후보는 `purchase/.incoming/`에 남겨 다음 실행의 매칭 후보와 skip 기준으로 재사용한다.

## 왜 이렇게 동작하나

구매 폴더 생성의 시작점은 전자세금계산서다. 견적서만 온 경우는 실제 구매로 이어지지 않을 수 있으므로, 견적서나 거래명세서만으로는 새 `purchase/<YYMMDD>_<업체명>/` 폴더를 만들지 않는다. 전자세금계산서가 먼저 오면 전세만 있는 incomplete 폴더라도 만든다. 이렇게 해야 나중에 견적서/거래명세서가 도착했을 때 기존 전세 파일을 잃지 않고 같은 폴더에 보강할 수 있다.

카드결제는 자동 검색 시작점으로 쓰지 않는다. 카드영수증 메일은 연구비 카드가 아닌 결제가 많이 섞일 수 있으므로, 사용자가 직접 `purchase/<YYMMDD>_<업체명>/` 폴더를 만들고 카드영수증을 넣은 경우에만 기존 폴더 보강 대상으로 본다. 이때 Gmail에서 수집된 견적서/거래명세서 후보가 업체명과 날짜 기준으로 맞으면 해당 폴더에 복사한다.

검색 범위는 기본 90일로 넓게 잡는다. 견적서/거래명세서가 전자세금계산서보다 먼저 오거나, 반대로 전자세금계산서가 먼저 오고 나중에 견적서/거래명세서가 오는 경우가 있어서다. 매일 90일을 다시 검색하되, 이미 처리한 첨부/링크는 `processed_sources`로 건너뛰어 반복 다운로드와 PDF 변환 비용을 줄인다. 먼저 도착했지만 아직 배치되지 않은 견적서/거래명세서는 `purchase/.incoming/`에 남아 이후 전자세금계산서가 들어왔을 때 매칭 후보로 쓰인다.

중복 처리 기준은 스레드가 아니라 개별 메시지와 첨부다. Gmail 답장은 같은 `thread_id`를 공유할 수 있으므로 스레드 전체를 처리 완료로 보면 새 답장 첨부를 놓칠 수 있다. 그래서 skip 기준은 `message_id + attachment_id`를 우선하고, 예전 메타처럼 attachment id가 없으면 `message_id + filename + size`를 사용한다.

PDF와 JSON 메타는 최종 구매 폴더 옆에 둔다. 아직 전자세금계산서 기준 케이스가 만들어지지 않은 후보만 `purchase/.incoming/`에 보관한다. 배치가 끝난 문서는 구매 폴더로 이동하고 DB 경로도 최종 위치로 갱신한다.

사업자등록증과 통장사본은 업체별 반복 문서로 취급한다. 한 번 확인된 파일은 `purchase/vendors/<업체명>/`에 저장해 새 구매 건에 재사용하고, 구매 케이스 폴더에는 필요한 경우 복사본을 둔다.

구매 케이스 상태는 세 단계다.

- `incomplete`: 완료 기준에 필요한 서류 중 일부만 있다.
- `ready`: 전자세금계산서, 견적서, 거래명세서가 있다.
- `finished`: 세금계산서형은 전자세금계산서, 견적서, 거래명세서, 사업자등록증, 통장사본이 모두 있고, 카드결제형은 영수증, 견적서, 거래명세서가 모두 있다.

먼저 검색/분류만 확인하려면:

```bash
python3 scripts/documents/run_daily.py --newer-than 2m --dry-run --no-labels
```

최종 저장 위치:

```text
purchase/
  documents.sqlite3
  vendors/
    에이이노텍/
      사업자등록증.pdf
      사업자등록증.json
      통장사본.pdf
      통장사본.json
  260617_에이이노텍/
    전세.pdf
    전세.json
    견적.pdf
    견적.json
    거명.pdf
    거명.json
    사업자등록증.pdf
    사업자등록증.json
    통장사본.pdf
    통장사본.json
```

HTML/XML 원본은 남기지 않는다. 변환된 PDF와 JSON 메타는 `purchase/.incoming/` 또는 최종 구매 폴더에만 남긴다.

문서 종류와 업체명은 메일 제목이 아니라 최종 PDF 본문을 우선한다. 특히 전자세금계산서는 PDF 안에 실제 `전자세금계산서`, 승인번호, 공급가액, 합계금액이 있는 경우에만 `tax_invoice`로 확정한다.

## 구매 폴더 체크

구매 폴더명 표준은 `YYMMDD_업체명`이다.

```bash
python3 scripts/documents/check_purchase_docs.py purchase/260618_에이이노텍
```

JSON 출력:

```bash
python3 scripts/documents/check_purchase_docs.py purchase/260618_에이이노텍 --format json
```

체크 대상 확장자는 `.pdf`, `.jpg`, `.jpeg`, `.png`, `.hwp`, `.hwpx`, `.xls`, `.xlsx`다. 파일명에서 `견적`, `거명`, `전세`, `사업자등록증`, `통장사본` 같은 키워드를 읽어 문서 존재 여부를 판단한다.

## 후보 추천

DB와 임시 수집 결과를 기준으로 구매 폴더에 맞을 가능성이 높은 후보를 추천한다. 보통 daily workflow를 쓰면 직접 실행할 일은 적다.

```bash
python3 scripts/documents/suggest_purchase_docs.py purchase/260618_에이이노텍
```

현재는 추천만 한다.

## 전자세금계산서 기준 자동 체크

수집된 전자세금계산서를 구매 케이스 기준으로 보고, 견적서/거래명세서는 금액 중심으로 매칭한다. 총액이 가장 강한 기준이고, 항목 정보가 있으면 항목 수와 항목별 공급가액 순서를 추가로 비교한다. 사업자등록증/통장사본은 업체 단위 재사용 문서로 매칭한다.

```bash
python3 scripts/documents/check_tax_invoice_cases.py
```

수동 입력 디렉터리의 문서 종류, 업체명, 작성일자, 금액 메타를 PDF 본문 기준으로 다시 채우려면:

```bash
python3 scripts/documents/reindex_archive.py
```

기본 재색인은 보관된 PDF의 내장 텍스트만 다시 읽는다. 기존 archive 전체에 대해 비싼 Codex fallback을 일괄 실행하지 않기 위해서다. 꼭 필요한 경우에만 아래처럼 명시한다.

```bash
python3 scripts/documents/reindex_archive.py --allow-codex
```

기존 구매 폴더와 예전 메타를 기준으로 처리 이력을 복원하려면:

```bash
python3 scripts/documents/backfill_processed_sources.py
```

이 처리는 현재 `purchase` 안의 PDF와 기존 메타 JSON의 해시가 일치하는 경우에만 `processed_sources`를 채운다. 스레드 전체를 skip하지 않고, 개별 `message_id + attachment_id` 또는 `message_id + filename + size` 기준으로 skip한다.

## Gmail 라벨

Gmail에는 아래 세 라벨만 사용한다.

```text
TaxInvoice/finished
TaxInvoice/processed
TaxInvoice/unprocessed
```

`TaxInvoice/processed`는 자동 처리 대상 문서가 있었고 수집/배치 흐름에서 처리된 메일이다. `TaxInvoice/unprocessed`는 후보로 검색됐지만 지원 첨부가 없거나 자동 처리 중 문제가 있어 사람이 확인해야 하는 메일이다. `TaxInvoice/finished`는 해당 메일의 문서가 속한 구매 케이스가 5종 필수 서류를 모두 갖춘 상태다.

라벨은 추가만 하지 않는다. 새 상태를 기록할 때 기존 관리 라벨인 `TaxInvoice/finished`, `TaxInvoice/processed`, `TaxInvoice/unprocessed`, 과거 라벨 `TaxInvoice/error`, `TaxInvoice/manual`, `Documents/*`를 먼저 제거하고 최종 상태 하나만 붙인다. Gmail 스레드 전체가 아니라 개별 메시지 기준으로 처리한다.

라벨을 붙인 메시지는 읽음 처리한다. 내부적으로 Gmail의 `UNREAD` 시스템 라벨을 제거한다.

기존 라벨을 정리하려면:

```bash
python3 scripts/documents/cleanup_gmail_labels.py
```

정리 스크립트는 과거 `TaxInvoice/error`, `TaxInvoice/manual`, `Documents/processed`, `Documents/error`, `Documents/manual` 라벨을 현재 정책으로 변환한다. `finished`가 이미 있으면 `finished`를 우선하고, `processed`와 `manual/error`가 같이 붙어 있던 메시지는 `processed`를 우선한다.

## purchase 폴더 자동 배치

`run_daily.py` 내부에서 호출하는 저수준 도구다. 임시 수집 디렉터리나 수동으로 지정한 입력 디렉터리를 대상으로 테스트할 때 사용한다. 기본은 dry run이다.

```bash
python3 scripts/documents/place_purchase_docs.py
```

실제로 폴더를 만들고 현재 확보한 PDF와 JSON 메타를 배치하려면:

```bash
python3 scripts/documents/place_purchase_docs.py --apply --sync-db --refresh-vendor-store --include-vendor-docs
```

## OCR 현황과 방향

OCR 실행은 `scripts/ocr`를 기준으로 통합한다. 신규 Gmail 수집 경로는 PDF/HTML/텍스트에서 내장 텍스트를 먼저 추출하고, 필요한 필드 검증에 실패하면 Codex image fallback을 사용한다. OCR API는 명시적으로 legacy 옵션을 선택한 경우에만 사용하며 기본 자동 경로에는 포함하지 않는다.

OCR은 두 단계로 분리한다.

1. 읽기: `scripts.ocr.extractors.extract_text()` 또는 `scripts.ocr.reader.read_document()`가 원문 텍스트/이미지 fallback을 담당한다.
2. 검증/파싱: 문서 종류별 validator가 금액, 업체명, 날짜, 품목 등 필요한 필드가 충분한지 판단한다.

이 분리는 중요하다. OCR 원문은 맞는데 날짜나 금액 파싱만 틀리는 경우가 있으므로, OCR 실행 성공과 업무 검증 성공을 같은 의미로 취급하지 않는다. `read_document()`는 각 attempt와 validation 결과를 남기고, 최종적으로 검증되지 않으면 `review_required`로 돌린다.

`scripts/paperwork/common/document_reader.py`는 기존 호출부 호환을 위한 얇은 wrapper로 유지한다. 새 OCR 실행과 Codex image/PDF 렌더링 구현은 `scripts/ocr`에 둔다.

다만 이 리팩터링은 영향 범위를 확인해야 한다. 최소 확인 대상은 다음과 같다.

- `scripts/paperwork/meeting/receipt_ocr.py`: 기존 회의비/출장비 영수증 처리 결과가 바뀌지 않아야 한다.
- `scripts/paperwork/meeting/process_receipts.py`: CLI 인자와 `records.csv` 저장 형식이 유지되어야 한다.
- `scripts/paperwork/common/document_reader.py`: 기존 OCR API, LiteLLM, Codex 호출 규약을 깨지 않아야 한다.
- `scripts/documents` 카드결제 흐름: OCR 메타가 있는 영수증만 금액 기반 매칭에 쓰고, OCR 실패 시 자동 매칭하지 않아야 한다.

## 주요 파일

- `run_daily.py`: 매일 실행할 단일 진입점이다.
- `collect_documents.py`: Gmail에서 구매 서류를 검색/수집한다.
- `check_purchase_docs.py`: 구매 폴더의 필수 서류 누락 여부를 확인한다.
- `suggest_purchase_docs.py`: 수집된 문서에서 구매 폴더별 후보를 추천한다.
- `check_tax_invoice_cases.py`: 전자세금계산서를 기준으로 수집 문서 완비 여부를 가격 기반으로 체크한다.
- `place_purchase_docs.py`: 수집 문서를 `purchase/YYMMDD_업체명/` 폴더로 자동 배치한다.
- `backfill_processed_sources.py`: 기존 구매 폴더와 예전 메타를 기준으로 처리 이력을 복원한다.
- `cleanup_gmail_labels.py`: Gmail 라벨을 `TaxInvoice/finished`, `TaxInvoice/processed`, `TaxInvoice/unprocessed`로 정리한다.
- `reindex_archive.py`: 수동 입력 디렉터리에서 금액/항목 가격 메타를 재추출한다.
- `collect_tax_invoices.py`: 기존 전자세금계산서 전용 수집기이며, 새 수집기에서 변환/인증 로직을 재사용한다.
- `development.md`: 구현 현황과 다음 개발 범위를 정리한 문서다.

## 현재 범위

구현된 범위는 Gmail 문서 수집, 임시 PDF 변환, 처리 완료 첨부/링크 skip, Gmail 라벨 정리, 전자세금계산서 기준 자동 매칭, incomplete 구매 폴더 생성과 보강, 세금계산서형/카드결제형 finished 처리, PDF/JSON 메타 배치, 업체별 사업자등록증/통장사본 재사용, SQLite 인덱싱, 구매 폴더 누락 체크다.

아직 구현하지 않은 범위는 카드영수증 OCR 기반 금액 매칭, cron/systemd 등록, 서류처리 사이트 업로드의 완전 자동화다.
