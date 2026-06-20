# 구매 서류 자동 수집 개발 문서

이 문서는 `scripts/documents/` 모듈의 현재 책임 범위와 구현 흐름을 설명한다. 목표는 Gmail에서 구매 처리에 필요한 서류를 찾아 `purchase/<YYMMDD>_<업체명>/`에 배치하고, 누락 여부를 확인할 수 있게 하는 것이다.

GUI, 이미지 업로드 기반 제출서류 생성, 서류처리 사이트 업로드는 이 모듈의 책임 범위 밖이다. 이 모듈은 그 기능들이 사용할 구매 폴더와 메타데이터를 준비한다.

## 1. 현재 책임 범위

구현된 범위:

- Gmail에서 전자세금계산서, 견적서, 거래명세서, 사업자등록증, 통장사본 후보 메일을 검색한다.
- 첨부 PDF, 홈택스 보안 HTML, 홈택스/XML성 첨부, allowlist 링크를 최종 PDF로 변환한다.
- 변환된 구매 문서 후보는 `purchase/.incoming/`에 저장해 다음 실행의 매칭 후보와 skip 기준으로 재사용한다.
- `documents/archive/`에는 더 이상 원본 캐시를 남기지 않는다.
- 최종 PDF와 JSON 메타는 구매 케이스 폴더 또는 업체 문서 폴더에 둔다.
- SQLite DB는 `purchase/documents.sqlite3`에 둔다.
- 이미 저장된 Gmail 첨부/링크는 `processed_sources`에 기록하고, 다음 실행에서 다운로드/변환 전에 건너뛴다.
- Gmail 라벨은 `TaxInvoice/finished`, `TaxInvoice/processed`, `TaxInvoice/unprocessed` 세 개만 사용한다.
- 전자세금계산서를 기준 케이스로 보고 견적서/거래명세서를 가격 기반으로 자동 매칭한다.
- 기존 `purchase/` 폴더에서 사업자등록증/통장사본을 발견하면 `purchase/vendors/<업체명>/`에 업체별 재사용 문서로 등록한다.
- Gmail에서 새 사업자등록증/통장사본을 받으면 같은 vendor 저장소에 등록한다.
- 새 구매 건이면 `purchase/<YYMMDD>_<업체명>/`를 만들고 `전세.pdf`, `견적.pdf`, `거명.pdf`, `사업자등록증.pdf`, `통장사본.pdf` 및 같은 이름의 `.json` 메타를 배치한다.
- `purchase/<case>/`를 스캔해 필수 서류 누락 여부를 확인한다.
- 단위 테스트는 `scripts/documents/tests/`에 둔다.

아직 구현하지 않은 범위:

- GUI에서 버튼으로 실행하거나 백그라운드에서 매일 실행하는 기능
- cron/systemd 등록
- 사업자등록증/통장사본 최신본 교체를 사람이 승인하는 UI
- 이미지 업로드를 받아 제출서류를 생성하는 기능
- 서류처리 사이트에 업로드하는 기능

## 2. 매일 실행할 단일 명령어

운영 시에는 아래 명령어 하나를 매일 아침 실행하면 된다.

```bash
HOMETAX_PASSWORD=1298207687 python3 scripts/documents/run_daily.py
```

기본 검색 범위는 `newer_than:90d`다. 견적서/거래명세서가 전자세금계산서보다 한참 먼저 오거나, 반대로 전자세금계산서가 먼저 오고 나중에 견적서/거래명세서가 오는 경우를 같은 검색창 안에서 다시 매칭하기 위해 넓게 잡는다.

```bash
HOMETAX_PASSWORD=1298207687 python3 scripts/documents/run_daily.py --newer-than 2m
```

실제 저장 없이 Gmail 검색/분류만 확인한다.

```bash
python3 scripts/documents/run_daily.py --newer-than 2m --dry-run --no-labels
```

## 3. 전체 workflow

`run_daily.py`가 수행하는 흐름은 다음과 같다.

1. `purchase/`를 스캔해 기존 구매 폴더 안의 사업자등록증/통장사본을 찾는다.
2. 발견한 반복 문서는 `purchase/vendors/<업체명>/`에 등록한다.
3. Gmail을 검색해 구매 문서 후보를 `purchase/.incoming/`에 PDF와 JSON으로 저장한다.
4. 이미 `processed_sources`에 있고 저장된 PDF가 존재하는 첨부/링크는 다시 다운로드하지 않는다.
5. 새로 저장된 사업자등록증/통장사본은 `purchase/vendors/<업체명>/`에 등록한다.
6. `purchase/vendors/<업체명>/`에 있는 사업자등록증/통장사본은 같은 업체 구매 폴더에 해당 문서 타입이 없을 때만 복사한다.
7. 이미 카드영수증이 들어 있는 기존 구매 폴더가 있으면 견적서/거래명세서 후보를 날짜와 업체명 기준으로 보강한다.
8. 전자세금계산서 PDF 메타를 기준으로 구매 케이스를 만든다. 견적서/거래명세서가 아직 없어도 전자세금계산서가 있으면 incomplete 폴더를 만든다.
9. 기존 incomplete 폴더가 있으면 견적서/거래명세서/업체 반복 문서를 자동 매칭해 같은 폴더에 보강한다.
10. 전자세금계산서, 견적서, 거래명세서, 업체별 사업자등록증/통장사본을 구매 폴더로 배치한다.
11. 각 PDF 옆에 같은 stem의 JSON 메타를 저장한다.
12. 구매 케이스 상태를 `incomplete`, `ready`, `finished`로 계산한다.
13. `purchase/documents.sqlite3`의 `documents`, `processed_sources`, `purchase_cases`를 갱신한다.
14. 완료 기준을 만족하면 관련 Gmail 메시지 라벨을 `TaxInvoice/finished`로 승격한다.
15. Gmail에는 최종 상태에 맞춰 `TaxInvoice/finished`, `TaxInvoice/processed`, `TaxInvoice/unprocessed` 중 하나만 남긴다.
16. 아직 배치되지 않은 후보는 `purchase/.incoming/`에 남겨 다음 실행에서 재사용한다.

## 4. 저장 구조

최종 저장 구조는 `purchase/` 아래로 모은다.

```text
purchase/
  documents.sqlite3
  vendors/
    에이이노텍/
      사업자등록증.pdf
      사업자등록증.json
      통장사본.pdf
      통장사본.json
    메타컴퍼니/
      사업자등록증.pdf
      사업자등록증.json
      통장사본.pdf
      통장사본.json
  260608_메타컴퍼니/
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

`purchase/vendors/`는 구매 케이스가 아니므로 `purchase_scan.py`에서 스캔 제외한다.

## 5. 구매 폴더명 규칙

표준 구매 폴더명은 아래 형식이다.

```text
purchase/<YYMMDD>_<업체명>/
```

예:

```text
purchase/260608_메타컴퍼니/
purchase/260609_엔티렉스/
purchase/260617_에이이노텍/
```

전제: 같은 날짜에 같은 업체 구매 건은 없다고 본다. 따라서 `YYMMDD_업체명`이 구매 건 식별자의 기본이 된다.

과거 폴더명인 `260406_optics`, `260618_pmmfa` 같은 legacy 형태는 날짜 중심으로 읽을 수는 있지만 업체 자동 매칭 신뢰도는 낮다.

## 6. 문서 종류

내부 `doc_type`은 영어 코드로 고정한다.

| doc_type | 파일명 | 설명 |
| --- | --- | --- |
| `tax_invoice` | `전세.pdf` | 전자세금계산서 |
| `estimate` | `견적.pdf` | 견적서 |
| `statement` | `거명.pdf` | 거래명세서 |
| `business_registration` | `사업자등록증.pdf` | 업체별 반복 문서 |
| `bankbook_copy` | `통장사본.pdf` | 업체별 반복 문서 |

필수 서류 체크는 위 5종을 기준으로 한다.

구매 케이스 상태:

- `incomplete`: 완료 기준에 필요한 서류 중 일부만 있다.
- `ready`: 전자세금계산서, 견적서, 거래명세서가 있다.
- `finished`: 세금계산서형은 전자세금계산서, 견적서, 거래명세서, 사업자등록증, 통장사본이 모두 있고, 카드결제형은 영수증, 견적서, 거래명세서가 모두 있다.

카드결제는 자동 검색 시작점으로 사용하지 않는다. 카드영수증 메일은 연구비 카드가 아닌 결제가 많이 섞일 수 있으므로, 사용자가 직접 카드영수증을 넣어 만든 기존 구매 폴더만 보강 대상으로 한다. 이 경우 `receipt`가 있는 폴더에 대해 Gmail/DB 후보 중 견적서와 거래명세서를 업체명과 날짜 기준으로 찾아 복사한다.

## 7. 매칭 규칙

전자세금계산서가 구매 케이스의 기준이다. 메일 제목보다 PDF 본문에서 추출한 값을 우선한다.

전자세금계산서 확정 조건:

- PDF 본문에 실제 전자세금계산서 표식이 있다.
- 승인번호, 공급가액, 합계금액 등 세금계산서다운 필드가 있다.
- 단순히 메일 제목이나 사업자등록증 본문에 `전자세금계산서`라는 문구가 있는 것만으로는 확정하지 않는다.

견적서/거래명세서 매칭 점수:

- 업체명이 같으면 가산한다.
- 총액이 같으면 가장 강하게 가산한다.
- 총액 차이가 1% 이내면 약하게 가산한다.
- 항목 수가 같으면 가산한다.
- 항목별 공급가액 순서가 같으면 가산한다.
- 문서번호/품목코드가 겹치면 가산한다.
- 작성일 또는 메일 날짜가 가까우면 가산한다.

사업자등록증/통장사본:

- 구매 건별 문서가 아니라 업체별 반복 문서로 취급한다.
- `purchase/vendors/<업체명>/`에 있으면 해당 업체의 새 구매 케이스에 복사한다.
- 기존 구매 폴더에서 발견한 파일은 vendor 저장소에 등록한다.
- Gmail에서 새로 발견한 파일도 vendor 저장소에 등록한다.

## 8. 주요 코드

```text
scripts/documents/
  run_daily.py                  # 매일 실행할 단일 진입점
  collect_documents.py          # Gmail 검색, 첨부/링크 PDF 변환
  collect_tax_invoices.py       # 홈택스 변환/인증 로직
  place_purchase_docs.py         # 구매 폴더 생성, 문서/메타 배치, vendor store 관리
  check_purchase_docs.py         # 구매 폴더 필수 서류 체크
  check_tax_invoice_cases.py     # 전자세금계산서 기준 매칭 상태 확인
  suggest_purchase_docs.py       # 후보 추천 보조 도구
  reindex_archive.py             # 임시/수동 입력 디렉터리 재색인 보조 도구
  backfill_processed_sources.py   # 기존 purchase/옛 메타 기반 처리 이력 복원
  cleanup_gmail_labels.py         # Gmail 라벨 정책 정리
  amounts.py                     # PDF 텍스트 금액/항목가격 추출
  classifiers.py                 # 문서 종류/업체/코드 추정
  purchase_scan.py               # purchase 폴더 스캔
  vendors.py                     # 업체명/폴더명 정규화
  db.py                          # SQLite 스키마와 upsert/load
  tests/
```

## 9. DB 위치와 테이블

DB 파일:

```text
purchase/documents.sqlite3
```

주요 테이블:

- `documents`: 최종 배치된 문서의 PDF/JSON 경로, Gmail 메타, 업체명, 금액, 항목 정보
- `processed_sources`: 처리 완료된 Gmail 첨부/링크 이력
- `purchase_cases`: 구매 케이스 폴더와 상태
- `purchase_documents`: `check_purchase_docs.py`가 로컬 폴더 스캔 결과를 기록

`documents.file_path`와 `documents.json_path`는 현재 저장 위치를 가리킨다. 아직 배치되지 않은 후보는 `purchase/.incoming/`를 가리키고, 구매 폴더로 이동되면 최종 위치로 갱신된다. daily workflow에서는 삭제될 임시 디렉터리 경로를 DB에 쓰지 않는다.

`processed_sources`는 스레드 단위가 아니라 첨부/링크 단위로 기록한다. Gmail 답장은 같은 `thread_id`를 공유할 수 있으므로 `thread_id`만으로 skip하면 새 답장 첨부를 놓칠 수 있다. 현재 skip 기준은 다음 순서다.

1. `source_key`
2. `gmail_message_id + source_attachment_id`
3. `gmail_message_id + source_filename + source_size`
4. 링크 문서는 `gmail_message_id + source_link`

`gmail_thread_id`는 디버깅과 묶어보기용 참고 정보로만 저장한다.

Gmail 라벨은 처리 상태 표시용으로만 쓴다. 현재 관리 라벨은 `TaxInvoice/finished`, `TaxInvoice/processed`, `TaxInvoice/unprocessed`이며, 과거 라벨 `TaxInvoice/error`, `TaxInvoice/manual`, `Documents/processed`, `Documents/error`, `Documents/manual`은 새 상태를 붙일 때 제거한다. 세 라벨이 동시에 붙지 않도록 `messages.modify`에서 `addLabelIds`와 `removeLabelIds`를 함께 보낸다. 라벨을 붙인 메시지는 `UNREAD` 시스템 라벨도 제거해 읽음 처리한다.

## 10. 보조 명령어

구매 폴더 누락 체크:

```bash
python3 scripts/documents/check_purchase_docs.py purchase
python3 scripts/documents/check_purchase_docs.py purchase/260608_메타컴퍼니 --format json
```

전자세금계산서 기준 매칭 상태 확인:

```bash
python3 scripts/documents/check_tax_invoice_cases.py
```

기존 파일 처리 이력 복원:

```bash
python3 scripts/documents/backfill_processed_sources.py
```

이 스크립트는 현재 `purchase` 안의 PDF와 기존 메타 JSON의 해시가 일치하는 경우에만 JSON을 구매 폴더 또는 `.incoming` 옆에 복원하고 `processed_sources`를 채운다. 단순히 옛 archive 경로만 존재하는 문서는 기본적으로 처리 완료로 보지 않는다.

Gmail 라벨 정리:

```bash
python3 scripts/documents/cleanup_gmail_labels.py
```

기존에 `TaxInvoice/manual`, `TaxInvoice/error`, `Documents/*`가 붙어 있던 메시지를 현재 정책으로 정리한다. `finished`가 이미 있으면 `finished`를 우선하고, `processed`와 다른 상태가 동시에 붙어 있던 메시지는 `processed`를 우선한다.

배치 계획만 확인:

```bash
python3 scripts/documents/place_purchase_docs.py
```

수동 입력 디렉터리에서 실제 배치:

```bash
python3 scripts/documents/place_purchase_docs.py \
  --archive purchase/.incoming \
  --apply \
  --sync-db \
  --refresh-vendor-store \
  --include-vendor-docs
```

## 11. 테스트

전체 테스트:

```bash
python3 -m unittest discover -s scripts/documents/tests
```

현재 테스트가 확인하는 핵심:

- 문서 종류 분류
- 전자세금계산서 PDF 본문 기준 분류
- 업체명 정규화
- 금액/항목 가격 추출
- `purchase/vendors`를 구매 케이스로 오인하지 않는지
- 기존 구매 폴더의 사업자등록증/통장사본을 vendor store에 등록하는지
- 배치 시 PDF와 JSON 메타가 구매 폴더로 같이 들어가는지
- 처리 이력이 message id 기준이라 같은 스레드의 새 답장 첨부를 skip하지 않는지
- 전자세금계산서만 있어도 incomplete 구매 폴더를 생성하는지
- 기존 incomplete 폴더에 견적서/거래명세서를 보강하는지
- 5종 필수 서류가 있어야 finished가 되는지

## 12. 다음 개발 후보

- `purchase/vendors/<업체명>/`의 기존 문서를 새 메일 첨부로 교체할 때 사람이 승인하는 UI
- daily 실행 결과 리포트 생성
- GUI에서 `run_daily.py` 실행 버튼과 누락 상태 표시
- 서류 생성 모듈이 `purchase/<case>/`의 PDF/JSON을 입력으로 쓰도록 연결
- 업로드 모듈이 완성된 `purchase/<case>/`를 제출 단위로 읽도록 연결
