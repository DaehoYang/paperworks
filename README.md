# Paperworks

회의비 영수증, 출장 교통비 영수증, 구매 견적서를 처리해 제출용 PDF와 업로드용 Excel 파일을 만드는 자동화 패키지다. 실행 코드는 `scripts/paperwork/`에 모아두고, 실제 데이터는 `meeting/`, `purchase/` 아래에 둔다.

기본 PDF 작성 방식은 **AcroForm 입력**이다. 회의록, 출장보고서, 물품검수확인서는 입력 가능한 PDF form에 값을 채운다. 영수증 이미지는 최종 PDF 뒤쪽에 첨부하며, 첨부용 이미지는 PDF 용량을 줄이기 위해 1MB 이하 JPEG로 압축한다. 원본 영수증 파일은 덮어쓰지 않는다.

## 설치

```bash
python3 -m pip install pypdf reportlab pillow pyyaml openpyxl xlrd xlwt
sudo apt-get update
sudo apt-get install -y poppler-utils
```

OCR API와 LiteLLM을 쓸 때:

```bash
export DHLAB_OCR_API_KEY="..."
export DHLAB_OCR_API_URL="https://dhlab.gachon.ac.kr/services/rag/ocr"
export DHLAB_LITELLM_API_KEY="..."
export DHLAB_LITELLM_BASE_URL="https://dhlab.gachon.ac.kr/services/litellm/v1"
export DHLAB_LITELLM_MODEL="local"
```

## 구조

```text
scripts/paperwork/
  assets/                  # 입력가능 PDF form assets
  common/                  # 공통 OCR, PDF text, LLM, schema 검증
  meeting/                 # 회의록/출장보고서 처리
  purchase/                # 물품검수확인서 처리

meeting/
  receipt/                 # 새 영수증 투입 위치
  receipt/used/            # 생성 완료 후 이동된 영수증
  receipt/ocr_text/        # OCR 원문
  receipt/records.csv      # 처리 ledger
  receipt/summary.csv      # 회의록 요약 및 참석자 분산 이력
  output/                  # 회의록/출장보고서 출력

purchase/
  <purchase_case>/          # 견적, 원본 검수서, 사진, 출력물
```

## Meeting / Trip

새 영수증은 `meeting/receipt/`에 넣고 실행한다.

```bash
python3 -m scripts.paperwork.meeting.process_receipts meeting/receipt/*.jpg meeting/receipt/*.png meeting/receipt/*.pdf
```

기본 동작:

- `food_drink`, `restaurant`, `cafe`, `meal`, `drink`: 회의록 생성
- `transport`: 왕복 pair를 찾아 출장보고서 생성
- 생성 완료된 입력 영수증은 `meeting/receipt/used/`로 이동
- 이동을 막고 싶으면 `--no-archive-receipts` 사용
- OCR 원문은 `meeting/receipt/ocr_text/`에 저장
- 처리하지 못한 파일은 `records.csv`에 `review`로 기록

출력:

- 회의록: `meeting/output/<YYMMDD>_<HHMM>_회의록.pdf`
- 출장보고서: `meeting/output/<YYMMDD>_출장보고서.pdf`

OCR 대신 구조화 JSON을 넣을 수 있다.

```bash
python3 -m scripts.paperwork.meeting.process_receipts \
  --metadata-json /tmp/receipt_records.json
```

엔진을 직접 지정할 수도 있다.

```bash
python3 -m scripts.paperwork.meeting.process_receipts meeting/receipt/receipt.jpg \
  --ocr-engine ocr-api-litellm

python3 -m scripts.paperwork.meeting.process_receipts meeting/receipt/receipt.jpg \
  --ocr-engine codex
```

### 회의록 규칙

`scripts/paperwork/assets/information.yml`에서 회의록과 출장보고서 설정을 관리한다.

- `project`: 과제번호, 연구책임자, 과제명, 연구기간
- `trip`: 출장보고서 연구책임자와 출장자 내역
- `members`: 내부 참석자 후보
- `external_members`: 특정 회의장소에서만 쓰는 외부 참석자
- `meeting_places`, `*_districts`: 주소 기반 회의장소 판정
- `topics`, `topic_order`: 일반 회의 topic
- `external_topics`, `external_topic_order`: 외부 공동연구 회의 topic
- `attendee_rules`: 회의 참석 인원 산정 규칙

회의일시는 영수증 시간 1시간 전을 기준으로 가장 가까운 30분 단위에서 시작하고, 영수증 시간을 포함하면서 최소 1시간 이상이 되도록 30분 단위로 종료한다.

회의 참석자 수:

- 기본값은 `ceil(total_price / attendee_rules.price_per_person)`
- `min_attendees`와 `max_attendees` 범위로 제한
- 음료만 있는 영수증은 음료 수량을 하한으로 사용
- `max_attendee_store_exceptions`에 등록된 매장은 지정 금액 이상이면 `max_attendees` 사용
- 현재 기본 예외: `쩡이네`, `쟁이네`, `평이네`가 100000원 이상이면 최대 참석자

참석자 선택:

- `fixed_first_attendee`는 항상 첫 번째 참석자
- 장소별 `external_members`는 먼저 고정
- 남은 자리는 `대학원생`을 우선 배치
- 대학원생끼리는 `summary.csv` 이력 기준으로 덜 나온 사람부터 선택
- 대학원생만으로 부족하면 다른 학생을 같은 방식으로 선택
- 그래도 부족할 때만 조교수 등 나머지 구성원 사용
- 동률일 때는 영수증 날짜와 파일명 기반 해시로 순서를 정해 재실행 결과를 유지

출장 pair 조건:

- 갈 때: 서울 출발
- 올 때: 서울 도착
- 같은 목적지
- 당일 또는 다음날 복귀
- pair를 찾지 못하면 `records.csv`에 기록 후 에러 종료

## Purchase

구매 폴더 예시:

```text
purchase/260401_optics/
  견적.pdf
  물품검수확인서.pdf
  imgs/
    1.jpg
    2.jpg
```

실행:

```bash
python3 -m scripts.paperwork.purchase.process_purchase purchase/260401_optics --inspection-date 2026-05-25
```

출력:

- `purchase/<case>/items.xls`
- `purchase/<case>/물품검수확인서_작성.pdf`

`--quote`를 생략하면 구매 폴더에서 파일명에 `견적` 또는 `견적서`가 들어간 파일을 자동 선택한다. `--images`를 생략하면 `imgs`, `imgs1`, `img` 순서로 사진 폴더를 찾는다. 날짜를 생략하면 실행일을 검수일로 쓴다.

견적서 파서:

- `auto`: PDF 텍스트 추출을 먼저 쓰고, 실패하면 OCR/LiteLLM 또는 Codex fallback
- `pdf-text`: `pdftotext` 결과만 사용
- `ocr-litellm`: OCR API 텍스트 + LiteLLM JSON 추출
- `codex`: Codex CLI로 견적서 JSON 추출

예:

```bash
python3 -m scripts.paperwork.purchase.process_purchase purchase/260401_optics --parse-engine auto

python3 -m scripts.paperwork.purchase.process_purchase purchase/260401_optics \
  --parse-engine ocr-litellm \
  --ocr-api-key "$DHLAB_OCR_API_KEY" \
  --litellm-api-key "$DHLAB_LITELLM_API_KEY"
```

### 구매 금액 규칙

`items.xls`는 `EzBaroItemReg_Sample.xls`와 같은 구형 Excel `.xls` 형식이며, 시트명은 `이지바로품목`이다.

- `순번`: 1부터 시작하는 항목 번호
- `품명`: 모델명 중심 품목명, 50자 이하
- `규격`: 상세 규격, 80자 이하
- `수량`: 구매 수량
- `단가`: 부가세 포함 1개 가격
- `공급가액`: 부가세 없는 N개 합계
- `부가세액`: `공급가액 * 10%`
- `총구입액`: `공급가액 + 부가세액`
- `용도설명`: 빈칸

견적서마다 표의 `단가`와 `공급가액` 의미가 다를 수 있으므로, 스크립트는 견적서 하단의 공급가액, VAT, 합계와 대조해서 가장 잘 맞는 해석을 자동 선택한다. 최종 `normalized_totals`가 견적서 하단 합계와 맞지 않으면 에러를 낸다.

업로드 오류를 막기 위해 저장 전에 문자열을 정리한다. `Ø`는 `D`로 바꾸고, 쉼표, 따옴표, slash, 등호 등 문제가 되는 특수문자를 제거한다.

## Form Assets

입력가능 PDF는 `scripts/paperwork/assets/`에서 관리한다.

```text
scripts/paperwork/assets/
  바나연회의록_빈칸.pdf
  바나연회의록_입력가능.pdf
  출장보고서.pdf
  출장보고서_입력가능.pdf
  물품검수확인서_입력가능.pdf
```

재생성:

```bash
python3 -m scripts.paperwork.meeting.create_minutes_form
python3 -m scripts.paperwork.meeting.create_trip_report_form
python3 -m scripts.paperwork.purchase.create_inspection_form purchase/260401_optics/물품검수확인서.pdf
```

처리와 동시에 물품검수확인서 form asset을 다시 만들려면:

```bash
python3 -m scripts.paperwork.purchase.process_purchase purchase/260401_optics --rebuild-form
```

Form 원칙:

- 원본 PDF는 그대로 두고 입력가능 PDF를 별도 생성
- 한글 입력을 위해 원본 PDF에 포함된 embedded 한글 폰트 사용
- 회의록은 AcroForm field value만 채우고 `/NeedAppearances=True`로 저장
- 출장보고서는 `pypdf` appearance 재생성 경로 사용
- 물품검수확인서는 AcroForm field value와 `NeedAppearances=True` 사용
- 사진과 영수증은 편집 필드가 아니라 PDF 이미지로 첨부

## 검증

문법 체크:

```bash
python3 -m py_compile $(find scripts/paperwork -name '*.py')
```

설정 체크:

```bash
python3 - <<'PY'
import yaml
from pathlib import Path
config = yaml.safe_load(Path("scripts/paperwork/assets/information.yml").read_text())
print("members", len(config["members"]))
print("meeting_places", list(config["meeting_places"]))
print("topics", list(config["topics"]))
PY
```

PDF 구조 확인:

```bash
pdfinfo meeting/output/<YYMMDD>_<HHMM>_회의록.pdf
pdfinfo meeting/output/<YYMMDD>_출장보고서.pdf
pdfinfo purchase/<case>/물품검수확인서_작성.pdf
```

PDF 렌더링 확인:

```bash
mkdir -p /tmp/pdf_check
pdftoppm -png -f 1 -l 3 -r 120 meeting/output/<file>.pdf /tmp/pdf_check/page
```

AcroForm 필드 확인:

```bash
python3 - <<'PY'
from pypdf import PdfReader
for path in [
    "meeting/output/260522_1230_회의록.pdf",
    "meeting/output/260421_출장보고서.pdf",
]:
    reader = PdfReader(path)
    acro = reader.trailer["/Root"].get("/AcroForm")
    print(path, "fields", len(reader.get_fields() or {}), "acro", bool(acro))
PY
```

## 문제 해결

- OCR API 호출 실패: `DHLAB_OCR_API_KEY`, `DHLAB_OCR_API_URL` 확인
- LiteLLM 호출 실패: `DHLAB_LITELLM_API_KEY`, `DHLAB_LITELLM_BASE_URL`, `DHLAB_LITELLM_MODEL` 확인
- 음식점/카페인데 review로 빠짐: metadata JSON에서 `receipt_type: food_drink`로 보정 후 재실행
- 출장 pair 에러: `origin`, `destination`, 날짜가 서울 출발/서울 도착 조건을 만족하는지 확인
- PDF가 뷰어마다 다르게 보임: AcroForm 필드 수와 `/NeedAppearances` 값을 먼저 확인하고, 필요하면 form asset을 재생성
