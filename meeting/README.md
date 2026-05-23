# Meeting receipt to minutes workflow

이 폴더는 영수증 이미지를 읽어 `receipt/summary.csv`를 채우고, `format/바나연회의록_빈칸.pdf` 서식에 회의 정보를 입력한 뒤 영수증 이미지를 첨부한 PDF를 만드는 작업용이다.

## 폴더 구조

- `format/바나연회의록_빈칸.pdf`: 회의록 PDF 원본 서식. AcroForm 필드가 들어 있다.
- `format/information.yml`: 과제 기본 정보와 참석자 후보 목록. 참석자별 소속/직위를 직접 입력한다.
- `receipt/`: 원본 영수증 이미지/PDF와 `summary.csv`.
- `output/`: 생성된 회의록 PDF와 회의록+영수증 결합 PDF.

## summary.csv

`summary.csv`는 단순 결과물이 아니라 작은 데이터베이스처럼 사용한다. 이미 처리한 파일의 topic, 참석자 수, OCR 엔진을 저장하고, 다음 실행 때 topic 자동 순환에 사용한다.

```csv
file_name,total_price,store_name,address,meeting_place,generated,topic,attendee_count,item_count,food_count,drink_count,attendee_names,ocr_engine
```

- `file_name`: `receipt/` 안의 원본 영수증 파일명.
- `total_price`: 영수증 총 결제금액. 쉼표 없이 숫자로 적는다.
- `store_name`: OCR/LLM이 추출한 상호명.
- `address`: OCR/LLM이 추출한 주소.
- `meeting_place`: 회의록에 들어갈 회의장소.
- `generated`: 영수증 결제일시. `YYYY-MM-DD HH:MM:SS` 형식을 쓴다.
- `topic`: `quantum`, `holography`, `deeplearning`, `cowork_DL`, `cowork_holo` 중 하나.
- `attendee_count`: 회의록 참석자 수.
- `item_count`: 주문한 음식/음료 전체 수량. 할인, 세금, 결제/승인 라인은 제외한다.
- `food_count`: 식사류/메인 메뉴 수량.
- `drink_count`: 음료 수량.
- `attendee_names`: 실제 회의록에 들어간 참석자명. `;`로 구분한다. 첫 번째 참석자는 항상 연구책임자인 `양대호`다.
- `ocr_engine`: `manual`, `codex`, `paddle-litellm` 중 하나.

## information.yml

과제 정보, 회의장소 판정, 회의내용, 참석자 수 규칙, 참석자 목록은 `format/information.yml` 한 파일에서 관리한다.

```yaml
project:
  과제번호: "202511000001"
  연구책임자: "박영서"
  연구과제명: "2025-2 가천바이오나노연구원 운영비"
  연구기간: "25-09-01 ~ 26-08-31"

meeting_places:
  gachon_univ: "바이오나노연구원 315호"
  seoul_west: "이화여자대학교 의과대학 1109호"
  ewha_mokdong: "이대목동병원 MCC A 지하 1층 세미나실"
  kriss: "한국표준과학연구원 313동 1층 회의공간"

gachon_univ_districts: ["성남시", "수정구", "분당구", "중원구", "송파구", "강남구", "서초구"]

seoul_west_districts: ["강서구", "은평구", "부천시", "김포시", "계양구", "마곡동", "발산동"]

ewha_mokdong_districts: ["양천구", "영등포구", "구로구", "금천구", "목동", "신정동", "여의도동"]

kriss_districts: ["대전광역시", "대전시", "유성구", "도룡동", "가정동", "신성동"]

topics:
  quantum:
    title: "양자광학 정기 랩미팅"
    content:
      - "최근 양자광학 연구 진행 상황 공유"
      - "앞으로의 연구 방향 논의"
      - "논문 작성 검토"

topic_order:
  - "quantum"
  - "holography"
  - "deeplearning"

external_topics:
  cowork_DL:
    title: "딥러닝 공동연구 논의"
    content:
      - "딥러닝을 다른 분야 연구에 적용가능성 논의"
      - "앞으로의 연구 방향 논의"
  cowork_holo:
    title: "홀로그래피 공동연구 논의"
    content:
      - "홀로그래피를 다른 분야 연구에 적용가능성 논의"
      - "앞으로의 연구 방향 논의"

external_topic_order:
  - "cowork_DL"
  - "cowork_holo"

attendee_rules:
  price_per_person: 30000
  max_attendees: 10
  fixed_first_attendee: "양대호"

members:
  - name: "양대호"
    department: "물리학과"
    position: "교수"
  - name: "황희성"
    department: "물리학과"
    position: "연구원"

external_members:
  - name: "손여주"
    department: "이화여자대학교"
    position: "조교수"
    meeting_places: ["seoul_west", "ewha_mokdong"]
```

참석자 항목:

- `name`: 회의록 성명 칸에 들어갈 이름.
- `department`: 회의록 소속 칸에 들어갈 소속.
- `position`: 회의록 직위 칸에 들어갈 직위.

기본 설정 입력은 이 YAML 파일 하나만 사용한다.

장소 판정:

- 주소가 `gachon_univ_districts` 중 하나를 포함하면 `meeting_places.gachon_univ`를 회의장소로 쓴다.
- 주소가 `ewha_mokdong_districts` 중 하나를 포함하면 `meeting_places.ewha_mokdong`를 회의장소로 쓴다.
- 주소가 `seoul_west_districts` 중 하나를 포함하면 `meeting_places.seoul_west`를 회의장소로 쓴다.
- 주소가 `kriss_districts` 중 하나를 포함하면 `meeting_places.kriss`를 회의장소로 쓴다.
- 둘 다 아니면 OCR/LLM이 추출한 음식점/카페명을 회의장소로 쓴다.
- 장소별 목록은 각 기준 장소를 중심으로 대략 반경 10km 안에 들어오는 시/구/동 키워드다.
- 이대목동병원과 이대서울병원/의과대학의 반경이 겹치는 서부권은 `ewha_mokdong_districts`를 먼저 검사해 더 가까운 장소로 보낸다.

회의내용과 순환 순서:

- `topics`에 회의제목과 회의내용을 추가/수정한다.
- `topic_order` 순서대로 `--topic auto`가 순환한다.
- `external_topics`와 `external_topic_order`는 `external_members`가 쓰이는 장소에서만 `--topic auto` 순환에 사용한다.
- `attendee_rules.fixed_first_attendee`는 항상 참석자 첫 줄에 배치된다.
- `external_members`는 `meeting_places`에 지정된 장소에서만 참석자 후보에 포함한다. 현재 손여주 프로필은 이대서울병원/의과대학과 이대목동병원 회의에서만 사용한다.

## 영수증 판독 규칙

영수증에서 최소한 다음 값을 읽는다.

- 결제 총액
- 결제일시
- 매장명
- 주소

회의 참석자 수는 총액과 품목 수를 함께 보고 정한다.

```text
금액 기준 참석자 수 = max(1, ceil(총액 / 30000))
식사+음료 혼합 품목 기준 = max(food_count, ceil(item_count / 2))
식사류만 있는 경우 품목 기준 = food_count
음료만 있는 경우 품목 기준 = drink_count
food_count/drink_count가 없고 item_count만 있으면 품목 기준 = item_count
최종 참석자 수 = min(10, max(금액 기준 참석자 수, 품목 기준))
```

예:

- `150000원`, 품목 수 미상: `5명`
- `28500원`, 품목 수 `2`: `2명`
- `45000원`, 메뉴 `2개` + 음료 `2개`: `max(2, ceil(4/2)) = 2명`
- `45000원`, 음료 `8개`: 금액 기준은 `2명`이지만 품목 기준으로 `8명`
- 품목 기준이나 금액 기준이 `10명`을 넘으면 현재 PDF 참석자 표 한계에 맞춰 `10명`

판별하기 쉬운 품목 수 룰:

- 메뉴명 옆 수량이 명확하면 수량 합계를 쓴다.
- 음식점에서 식사류와 음료가 섞이면 음료를 그대로 인원으로 더하지 않고 `max(food_count, ceil(item_count / 2))`를 쓴다.
- 카페/음료만 있는 영수증은 음료 컵 수를 참석자 수의 하한으로 본다.
- 세금, 할인, 합계, 카드, 승인번호, 멤버십, 쿠폰, 포인트, 영수증 문구는 품목 수에서 제외한다.
- 품목 수가 OCR에서 불명확하면 금액 기준만 쓴다.

## 회의장소 규칙

회의록의 회의장소는 음식점/카페명이 아니라 아래 규칙으로 정한다. 실제 키워드와 장소명은 `information.yml`에서 수정한다.

- 주소가 `gachon_univ_districts`에 해당하는 경우: `meeting_places.gachon_univ`
- 주소가 `ewha_mokdong_districts`에 해당하는 경우: `meeting_places.ewha_mokdong`
- 주소가 `seoul_west_districts`에 해당하는 경우: `meeting_places.seoul_west`
- 주소가 `kriss_districts`에 해당하는 경우: `meeting_places.kriss`
- 그 외 지역인 경우: 영수증의 음식점/카페 이름

`gachon_univ_districts`는 가천대학교 글로벌캠퍼스 주소를 중심으로 대략 반경 10km 안에 들어오는 성남, 송파, 강남, 서초, 강동, 하남, 광주, 용인 수지 일부 키워드를 포함한다.

`seoul_west_districts`는 이화여자대학교 의과대학 주소를 중심으로 대략 반경 10km 안에 들어오면서 이대목동병원보다 마곡 쪽에 가까운 강서, 은평, 부천, 고양 덕양, 김포, 계양 일부 키워드를 포함한다.

`ewha_mokdong_districts`는 이대목동병원 주소를 중심으로 대략 반경 10km 안에 들어오면서 이대서울병원/의과대학보다 목동 쪽에 가까운 양천, 영등포, 구로, 금천, 마포, 서대문 일부 키워드를 포함한다.

`kriss_districts`는 한국표준과학연구원 주소를 중심으로 대전 유성구 연구단지 주변 키워드를 포함한다.

## 회의내용 후보

회의제목과 회의내용은 `information.yml`의 `topics`에서 관리한다. 기본값은 아래 셋이다. 이대서울병원/의과대학 또는 이대목동병원 회의처럼 외부 참석자가 들어가는 경우에는 `external_topics`를 사용한다.

### 양자광학 정기 랩미팅

```text
1. 최근 양자광학 연구 진행 상황 공유
2. 앞으로의 연구 방향 논의
3. 논문 작성 검토
```

### 홀로그래피 정기 랩미팅

```text
1. 최근 홀로그래피 연구 진행 상황 공유
2. 앞으로의 연구 방향 논의
3. 논문 작성 검토
```

### 딥러닝 연구 정기 랩미팅

```text
1. 최근 딥러닝 응용 연구 진행 상황 공유
2. 앞으로의 연구 방향 논의
3. 논문 작성 검토
```

외부 공동연구 topic:

- `cowork_DL`: 딥러닝 공동연구 논의
- `cowork_holo`: 홀로그래피 공동연구 논의

`--topic auto`를 쓰면 일반 회의는 `summary.csv`에 저장된 마지막 topic을 보고 `quantum -> holography -> deeplearning -> quantum` 순서로 순환한다. 외부 참석자가 들어가는 이대서울병원/의과대학 또는 이대목동병원 회의는 `cowork_DL -> cowork_holo -> cowork_DL` 순서로 순환한다.

## PDF 서식 필드

`format/바나연회의록_빈칸.pdf`의 주요 필드명은 다음과 같다.

- 과제 기본 정보: `과제번호`, `연구책임자`, `연구과제명`, `연구기간`
- 회의 정보: `회의제목`, `회의일시`, `회의장소`, `회의내용`
- 참석자: `소속1`부터 `소속10`, `직위1`부터 `직위10`, `성명1`부터 `성명10`

현재 서식은 A4 1페이지이고, 생성 결과는 보통 다음 2개 파일이다.

- `output/<receipt_stem>_바나연회의록.pdf`
- `output/<receipt_stem>_바나연회의록_영수증첨부.pdf`

두 번째 파일은 1쪽 회의록, 2쪽 영수증 이미지로 구성한다.

## 현재 예시

`receipt/20260406_110918.jpg`는 다음처럼 처리했다.

- 총액: `28500`
- 참석자 수: `1`
- 영수증 주소: 서울특별시 강서구 마곡중앙로
- 회의장소: `이화여자대학교 의과대학 1109호`
- 회의제목: `양자광학 정기 랩미팅`
- 결제일시: `2026-04-06 09:09:14`

생성 파일:

- `output/20260406_110918_바나연회의록.pdf`
- `output/20260406_110918_바나연회의록_영수증첨부.pdf`

## 자동 생성 스크립트

반복 작업은 `generate_minutes.py`로 처리한다. `--ocr`을 쓰면 Codex CLI vision으로 영수증 이미지에서 금액/일시/상호/주소를 읽고, 나머지 변환을 이어서 처리한다.

필요 패키지:

```bash
python3 -m pip install pypdf reportlab pillow pyyaml
```

예시:

```bash
python3 meeting/generate_minutes.py 20260406_110918.jpg \
  --ocr \
  --topic auto
```

OCR 결과를 사람이 일부만 고쳐 넣고 싶으면 같은 옵션을 덮어쓸 수 있다. 예를 들어 결제일시만 직접 지정하려면 다음처럼 실행한다.

```bash
python3 meeting/generate_minutes.py 20260406_110918.jpg \
  --ocr \
  --generated "2026-04-06 09:09:14" \
  --topic auto
```

OCR을 쓰지 않는 수동 입력 예시:

```bash
python3 meeting/generate_minutes.py 20260406_110918.jpg \
  --total-price 28500 \
  --generated "2026-04-06 09:09:14" \
  --store-name "봉추&비빔밥 (마곡나루역점)" \
  --address "서울특별시 강서구 마곡중앙로 161-8" \
  --topic auto \
  --item-count 2
```

`--topic` 값은 다음 중 하나다.

- `auto`: `summary.csv`를 보고 topic을 순환 선택
- `quantum`: 양자광학 정기 랩미팅
- `holography`: 홀로그래피 정기 랩미팅
- `deeplearning`: 딥러닝 연구 정기 랩미팅
- `cowork_DL`: 딥러닝 공동연구 논의
- `cowork_holo`: 홀로그래피 공동연구 논의

OCR 모델은 `--ocr-model`로 지정할 수 있다.

```bash
python3 meeting/generate_minutes.py 20260406_110918.jpg \
  --ocr \
  --ocr-model gpt-5.4-mini \
  --topic quantum
```

현재 Codex CLI 모델 카탈로그 기준으로 `gpt-5-mini`는 이 ChatGPT 계정에서 지원되지 않고, 실제 경량 후보는 `gpt-5.4-mini`다. 다만 샘플 테스트에서는 `gpt-5.4-mini`가 빠르거나 비슷한 경우도 있지만, 금액/상호/주소를 틀린 사례가 있었다. 비용이 더 중요하고 결과를 사람이 확인할 수 있으면 `gpt-5.4-mini`, 자동 일괄 처리에서는 기본값인 `gpt-5.5`를 권장한다.

### PaddleOCR + LiteLLM OCR

Codex vision 대신 로컬 PaddleOCR로 텍스트만 추출하고, `dhlab.gachon.ac.kr/services/litellm/`의 qwen 라우팅으로 영수증 필드를 추출할 수도 있다.

```bash
export DHLAB_LITELLM_API_KEY="..."
PYTHONDONTWRITEBYTECODE=1 \
LD_LIBRARY_PATH=/opt/conda/lib:$LD_LIBRARY_PATH \
python3 meeting/generate_minutes.py 20260406_110918.jpg \
  --ocr \
  --ocr-engine paddle-litellm \
  --topic auto
```

테스트한 컨테이너에서는 GPU가 없어서 CPU로 실행했다. `paddleocr 3.5.0`/`paddlepaddle 3.3.1` 조합은 CPU oneDNN 런타임 에러가 났고, 실제 동작한 조합은 다음이다.

```bash
python3 -m pip install \
  paddlepaddle==2.6.2 \
  paddleocr==2.7.3 \
  numpy==1.26.4
```

PaddleOCR는 EXIF orientation을 직접 반영하지 않는 경우가 있어서, 스크립트 내부에서 `ImageOps.exif_transpose`로 보정한 임시 이미지를 OCR한다. LiteLLM 호출에는 `extra_body.chat_template_kwargs.enable_thinking=false`를 넣어 qwen의 reasoning 출력을 끄고 JSON만 받는다.

샘플 테스트 결과:

- `20260406_110918.jpg`: 금액 `28500`, 날짜 정상. 상호는 OCR 노이즈가 있지만 `강서구` 주소로 서울 서부 회의장소 판정 성공.
- `20260407_220403.jpg`: 금액 `44500`, 날짜 정상. `수정구` 주소로 성남 회의장소 판정 성공.
- `KakaoTalk_20260321_183313480.jpg`: 카드 결제금액 `72080`, 날짜/상호 대체로 정상.

이 경로는 비용이 낮지만 상호명은 OCR 품질에 영향을 많이 받는다. 회의장소 판정과 참석자 수 계산에는 충분히 쓸 만하고, 상호명까지 정확해야 하는 경우에는 Codex vision fallback을 권장한다.

참석자 선택 규칙:

- 1번 참석자는 항상 연구책임자인 `양대호`로 고정한다.
- 나머지 참석자는 `summary.csv`의 기존 `attendee_names` 이력을 보고, 최근에 덜 사용된 사람을 우선 배치한다.
- 무작위 함수는 쓰지 않고 영수증 날짜/파일명 기반으로 순환시켜, 같은 입력을 다시 처리해도 결과가 재현되게 한다.

스크립트가 자동 처리하는 항목:

- `summary.csv` 행 추가 또는 갱신
- 총액 기준 참석자 수 계산
- 주소 기준 회의장소 결정
- Codex CLI OCR 사용 시 영수증 금액/일시/상호/주소 추출
- `information.yml`에서 과제 정보와 참석자 후보 읽기
- PDF 서식 필드 좌표 추출
- 회의록 PDF 생성
- 영수증 이미지를 2쪽으로 붙인 결합 PDF 생성

## 검증

생성 후 아래를 확인한다.

```bash
sed -n '1,20p' meeting/receipt/summary.csv
pdfinfo meeting/output/<receipt_stem>_바나연회의록_영수증첨부.pdf
pdftotext -layout meeting/output/<receipt_stem>_바나연회의록.pdf -
```

이미지 렌더링 확인이 필요하면 다음처럼 PNG로 변환해 확인한다.

```bash
mkdir -p /tmp/meeting_check
pdftoppm -png -f 1 -l 2 -r 100 \
  meeting/output/<receipt_stem>_바나연회의록_영수증첨부.pdf \
  /tmp/meeting_check/page
```
