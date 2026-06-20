# Paperworks

가천대 연구비 행정 처리를 위한 자동화 도구다. Gmail에서 구매 증빙을 수집해 `purchase/` 폴더를 정리하고, 회의비/출장비 영수증과 구매 검수 서류를 생성하며, 준비된 구매 건을 포털에 업로드한다.

## 설치

저장소 루트에서 실행한다.

```bash
python3 -m pip install --user -r scripts/documents/requirements.txt
python3 -m pip install --user -r scripts/gui/requirements.txt
python3 -m pip install --user pypdf reportlab pillow pyyaml openpyxl xlrd xlwt playwright
python3 -m playwright install chromium
sudo apt-get update
sudo apt-get install -y poppler-utils
```

React GUI를 쓸 때:

```bash
cd scripts/gui/frontend
npm install
npm run build
```

필요한 로컬 설정 파일:

```text
credentials.json          # Gmail OAuth desktop app credential
secret.json               # 포털 로그인 정보
projects.yml              # 과제 번호/과제명/기본 검수자 정보
```

`credentials.json`, `secret.json`, token, API key는 커밋하지 않는다.

## 주요 폴더

```text
purchase/
  .incoming/              # Gmail에서 수집했지만 아직 배치 전인 후보
  .incoming/originals/    # 결합 PDF 원본 보존 위치
  documents.sqlite3       # 문서/처리 이력 DB
  vendors/                # 업체별 사업자등록증/통장사본 재사용 저장소
  <YYMMDD>_<업체명>/       # 구매 케이스

meeting/
  receipt/                # 새 영수증 투입 위치
  receipt/used/           # 처리 완료 영수증
  output/                 # 회의록/출장보고서 출력
```

구매 케이스 표준 파일명:

```text
전세.pdf                  # 전자세금계산서
견적.pdf                  # 견적서
거명.pdf                  # 거래명세서
사업자등록증.pdf
통장사본.pdf
items.xls
물품검수확인서_작성.pdf
```

## React GUI

React/FastAPI GUI는 Dashboard, 파일 브라우저, 작업 로그, 이미지 업로드 도우미를 제공한다.

```bash
python3 scripts/gui/run_react.py --port 45001 --detach
```

Jupyter proxy 환경에서는 다음 주소로 접속한다.

```text
https://dhlab.gachon.ac.kr/user/sheepvs5/proxy/45001/
```

GUI에서 할 수 있는 일:

- `purchase/`, `meeting/` 파일 탐색과 PDF preview
- 파일 업로드, rename, move/copy, download
- Dashboard에서 구매 케이스 상태 확인
- 구매 사진을 품목 번호에 맞춰 정리
- 장시간 작업의 job log 확인

파일 API는 `purchase/`와 `meeting/`만 노출한다. dotfile, token, credential, secret 파일은 차단하고, 삭제는 `scripts/gui/trash/`로 이동한다.

## 구매 문서 수집

매일 실행할 기본 명령:

```bash
HOMETAX_PASSWORD=1234567890 python3 scripts/documents/run_daily.py
```

기본 Gmail 검색 범위는 최근 90일이다. 견적서/거래명세서와 전자세금계산서가 몇 주 간격으로 도착하는 경우가 있어서 넓게 잡는다. 범위를 바꾸려면:

```bash
HOMETAX_PASSWORD=1234567890 python3 scripts/documents/run_daily.py --newer-than 2m
```

저장 없이 검색/분류만 확인:

```bash
python3 scripts/documents/run_daily.py --newer-than 2m --dry-run --no-labels
```

동작 요약:

1. Gmail에서 전자세금계산서, 견적서, 거래명세서, 사업자등록증, 통장사본 후보를 찾는다.
2. 이미 처리된 Gmail 첨부/링크는 `processed_sources`와 실제 저장 파일을 기준으로 건너뛴다.
3. 새 문서는 PDF와 JSON metadata로 `purchase/.incoming/`에 저장한다.
4. 결합 PDF는 원본을 보존하고, 필요한 경우 페이지/range 단위로 분할해 검증된 조각만 저장한다.
5. 전자세금계산서를 기준으로 구매 폴더를 만들거나 기존 incomplete 폴더를 보강한다.
6. 사업자등록증/통장사본은 `purchase/vendors/<업체명>/`에 저장하고 같은 업체 구매 폴더에 복사한다.
7. 구매 케이스 상태와 SQLite DB를 갱신한다.
8. 완료된 Gmail 메시지는 `TaxInvoice/finished` 라벨로 승격한다.

Gmail 라벨은 아래 세 개만 사용한다.

```text
TaxInvoice/finished
TaxInvoice/processed
TaxInvoice/unprocessed
```

기존 라벨 정리:

```bash
python3 scripts/documents/cleanup_gmail_labels.py
```

## 문서 분류와 결합 PDF 처리

기존 빠른 분류는 유지한다.

- 메일 제목/본문/보낸 사람
- 첨부파일명
- PDF 내장 텍스트

OCR/page split은 필요한 PDF에만 fallback으로 실행한다. split trigger는 다음 구조다.

```text
PDF가 2페이지 이상
그리고 다음 중 하나 이상:
  - all_doc_types가 2개 이상
  - tax_invoice 또는 vendor 문서 validation 실패
  - PDF 전체 텍스트에 서로 다른 문서 시작 신호가 2개 이상 있음
  - 파일명/메일 제목에 결합 문서 힌트가 있음
```

split 대상 문서:

```text
tax_invoice
estimate
statement
business_registration
bankbook_copy
```

분할 방식:

1. PDF를 페이지별로 나눈다.
2. 각 페이지를 분석해 doc_type 후보와 validation 결과를 만든다.
3. 연속 페이지를 segment로 묶는다.
4. segment PDF를 다시 만들고 segment 단위로 validation한다.
5. 통과한 segment만 `.incoming`에 저장한다.
6. 원본 결합 PDF는 `.incoming/originals/`에 보존한다.

`estimate`, `statement`는 품목/수량/단가/금액 같은 continuation 신호가 있으면 다음 페이지를 붙일 수 있다. `tax_invoice`도 전자계산서/합계금액/공급가액/승인번호 같은 신호가 있으면 continuation을 허용한다. `business_registration`, `bankbook_copy`는 기본적으로 1페이지 문서로 본다.

전자세금계산서 validation 기준:

```text
metadata:
  vendor
  issue_date
  amount

PDF text:
  /전자.{0,20}계산서/
  합계금액
```

이 기준은 `전자세금계산서`, `전자수정세금계산서`, `전자(세금)계산서`를 모두 허용한다.

## 구매 폴더 상태 확인

전체 purchase 상태:

```bash
python3 scripts/documents/check_purchase_docs.py purchase
```

특정 구매 폴더:

```bash
python3 scripts/documents/check_purchase_docs.py purchase/260608_메타컴퍼니 --format json
```

상태 의미:

- `incomplete`: 필요한 문서 일부가 없다.
- `ready`: 전자세금계산서, 견적서, 거래명세서가 있다.
- `finished`: 세금계산서형은 5종 문서가 모두 있고, 카드결제형은 영수증, 견적서, 거래명세서가 있다.

전자세금계산서 기준 매칭 상태 확인:

```bash
python3 scripts/documents/check_tax_invoice_cases.py
```

수동 배치 계획 확인:

```bash
python3 scripts/documents/place_purchase_docs.py
```

수동 배치 적용:

```bash
python3 scripts/documents/place_purchase_docs.py \
  --archive purchase/.incoming \
  --apply \
  --sync-db \
  --refresh-vendor-store \
  --include-vendor-docs
```

## 구매 검수 서류 생성

구매 폴더에 견적서와 사진을 준비한 뒤 실행한다.

```bash
python3 -m scripts.paperwork.purchase.process_purchase \
  purchase/260608_메타컴퍼니 \
  --project-id 202601800001
```

출력:

```text
purchase/<case>/items.xls
purchase/<case>/물품검수확인서_작성.pdf
```

`--quote`를 생략하면 구매 폴더에서 `견적` 또는 `견적서`가 들어간 파일을 고른다. `--images`를 생략하면 `imgs`, `imgs1`, `img`, 케이스 폴더 순서로 사진을 찾는다.

견적서 parser 지정:

```bash
python3 -m scripts.paperwork.purchase.process_purchase purchase/<case> --parse-engine auto
python3 -m scripts.paperwork.purchase.process_purchase purchase/<case> --parse-engine pdf-text
python3 -m scripts.paperwork.purchase.process_purchase purchase/<case> --parse-engine codex
```

## 회의비/출장비 영수증 처리

새 영수증을 `meeting/receipt/`에 넣고 실행한다.

```bash
python3 -m scripts.paperwork.meeting.process_receipts \
  meeting/receipt/*.jpg meeting/receipt/*.png meeting/receipt/*.pdf
```

출력:

```text
meeting/output/<YYMMDD>_<HHMM>_회의록.pdf
meeting/output/<YYMMDD>_출장보고서.pdf
```

처리 완료 영수증은 기본적으로 `meeting/receipt/used/`로 이동한다. 이동을 막으려면:

```bash
python3 -m scripts.paperwork.meeting.process_receipts meeting/receipt/*.jpg --no-archive-receipts
```

OCR engine 지정:

```bash
python3 -m scripts.paperwork.meeting.process_receipts meeting/receipt/receipt.jpg --ocr-engine codex
python3 -m scripts.paperwork.meeting.process_receipts meeting/receipt/receipt.jpg --ocr-engine ocr-api-litellm
```

회의록/출장보고서 규칙은 `scripts/paperwork/assets/information.yml`에서 관리한다.

## 포털 업로드

사전검사:

```bash
python3 scripts/upload/gachon_portal_upload.py \
  --project-id 202601800001 \
  --case-dir purchase/260608_메타컴퍼니 \
  --step preflight
```

임시저장까지 실행:

```bash
python3 scripts/upload/gachon_portal_upload.py \
  --project-id 202601800001 \
  --case-dir purchase/260608_메타컴퍼니 \
  --step fill-save
```

저장 후 바로 신청까지 시도:

```bash
python3 scripts/upload/gachon_portal_upload.py \
  --project-id 202601800001 \
  --case-dir purchase/260608_메타컴퍼니 \
  --step fill-submit
```

여러 구매 건을 하나의 청구서에 묶기:

```bash
python3 scripts/upload/gachon_portal_upload.py \
  --project-id 202601800001 \
  --case-dir purchase/260608_메타컴퍼니 \
  --case-dir purchase/260609_엔티렉스 \
  --step fill-save
```

브라우저를 보면서 단계별 실행:

```bash
python3 scripts/upload/gachon_portal_upload.py --interactive --headed
```

주요 옵션:

- `--project-id`: `projects.yml`의 과제 key 또는 과제번호
- `--case-dir`: 구매 폴더. 여러 번 지정 가능
- `--draft-total`: 기존 임시저장 건을 열 때 사용할 금액
- `--secret`: 포털 로그인 정보 파일
- `--profile`: Playwright persistent profile 경로
- `--headed`: 브라우저 창 표시
- `--step`: `preflight`, `fill-save`, `fill-submit`, `submit-draft`, `append-draft` 등

포털 서버 검증에서 예산 초과, 권한 부족, 필수 입력 누락, 거래처 정보 오류가 나오면 자동화는 신청 완료로 보지 않고 중단한다.

## OCR reader

공통 OCR reader는 `scripts/ocr`에 있다. 기본 순서:

1. PDF/HTML/text 내장 텍스트 추출
2. 필요한 필드 validation 실패 시 Codex image fallback

OCR API는 기본 경로가 아니며, 명시적으로 method를 넣을 때만 사용한다.

수동 테스트:

```bash
python3 -m scripts.ocr.cli path/to/document.pdf \
  --doc-type estimate \
  --output /tmp/document.read.json \
  --overwrite
```

## 검증

문서 수집 테스트:

```bash
python3 -m unittest discover -s scripts/documents/tests
```

주요 Python 문법 검사:

```bash
python3 -m py_compile \
  scripts/documents/collect_documents.py \
  scripts/documents/ocr_metadata.py \
  scripts/upload/gachon_portal_upload.py \
  scripts/paperwork/purchase/process_purchase.py
```

PDF 구조 확인:

```bash
pdfinfo purchase/<case>/물품검수확인서_작성.pdf
pdfinfo meeting/output/<file>.pdf
```

PDF 렌더링 확인:

```bash
mkdir -p /tmp/pdf_check
pdftoppm -png -f 1 -l 3 -r 120 purchase/<case>/물품검수확인서_작성.pdf /tmp/pdf_check/page
```
