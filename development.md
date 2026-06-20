# Paperworks Development

이 문서는 구현 세부 사용법이 아니라 개발 원칙과 앞으로 구현할 항목을 정리한다. 사용법은 루트 `README.md`를 기준으로 한다.

## 설계 철학

Paperworks의 핵심 목표는 행정 처리에서 반복되는 문서 수집, 분류, 생성, 업로드를 자동화하되, 잘못된 문서가 조용히 섞이지 않게 하는 것이다.

중요한 원칙:

- **원본 보존**: 메일에서 받은 원본 PDF는 가능하면 보존한다. 결합 PDF를 분할해도 원본은 `purchase/.incoming/originals/`에 남긴다.
- **검증 기반 자동화**: OCR 성공만으로 문서를 확정하지 않는다. 문서 타입별 필수 필드 validation을 통과해야 다음 단계로 보낸다.
- **빠른 경로 우선**: 파일명, 메일 본문, PDF 내장 텍스트로 먼저 판단한다. OCR/image fallback은 validation 실패나 결합 문서 의심이 있을 때만 실행한다.
- **반복 다운로드 방지**: Gmail source는 `message_id + attachment_id` 또는 대체 source key로 추적한다. 같은 첨부를 매번 다시 OCR하지 않는다.
- **업체 문서는 hard gate**: 사업자등록증과 통장사본은 vendor store에 들어가면 이후 구매 건에 반복 전파된다. 따라서 vendor store 저장/복사 전 validation을 통과해야 한다.
- **전자세금계산서 우선**: 구매 케이스 생성 기준은 전자세금계산서다. 견적서/거래명세서만으로 새 세금계산서형 구매 폴더를 만들지 않는다.
- **기존 폴더 비파괴**: 새 로직은 신규 수집 문서에 적용한다. 기존 purchase 폴더 문서를 대량 rewrite하거나 자동 migration하지 않는다.
- **UI는 작업대**: GUI는 파일 탐색기가 아니라 상태 확인, 일괄 실행, 로그 확인, 사람이 필요한 검토를 한 화면에서 처리하는 작업대가 되어야 한다.
- **secret 비노출**: token, credential, secret, API key는 UI, log, Codex prompt에 노출하지 않는다.

## 현재 아키텍처

주요 모듈:

```text
scripts/documents/        Gmail 문서 수집, 분류, 배치, DB 관리
scripts/ocr/              공통 문서 reader와 OCR fallback
scripts/paperwork/        회의/출장/구매 제출 서류 생성
scripts/upload/           가천대 포털 업로드 자동화
scripts/gui/              React/FastAPI GUI와 job runner
```

구매 문서 흐름:

```text
Gmail search
  -> attachment/link PDF conversion
  -> fast classify
  -> validation
  -> page/range split fallback if needed
  -> .incoming candidate
  -> vendor store install if vendor doc
  -> tax_invoice based placement
  -> purchase/<YYMMDD>_<vendor>/
  -> DB + Gmail label update
```

전자세금계산서 validation 기준:

```text
metadata: vendor, issue_date, amount
PDF text: /전자.{0,20}계산서/, 합계금액
```

결합 PDF 처리:

- `tax_invoice`, `estimate`, `statement`, `business_registration`, `bankbook_copy`를 split 대상 타입으로 본다.
- PDF 2페이지 이상이면서 다중 타입, validation 실패, 다중 문서 시작 신호, 파일명/제목 결합 힌트 중 하나가 있으면 split을 시도한다.
- 페이지별 분석 뒤 연속 페이지를 segment로 묶고, segment PDF를 다시 만들어 validation한다.
- 통과한 segment만 저장하고 원본은 보존한다.

## 구현해야 하는 항목

### 1. Review Required Queue

현재 validation 실패 segment는 자동 저장하지 않거나 fallback으로 넘어간다. 사람이 봐야 하는 문서를 별도 queue로 남겨야 한다.

필요한 동작:

- `purchase/.incoming/review_required/` 저장소 생성
- 실패한 segment PDF와 JSON 저장
- 원본 결합 PDF는 `review_required/originals/` 또는 기존 `.incoming/originals/`와 연결
- JSON 필드:
  - `status: review_required`
  - `reason`
  - `missing_fields`
  - `invalid_fields`
  - `candidate_doc_types`
  - `source_original_pdf`
  - `source_page_start`
  - `source_page_end`
  - Gmail/source metadata

GUI:

- Dashboard metric에 `Review required` 추가
- 클릭 시 review viewer 열기
- 왼쪽은 review item list, 오른쪽은 PDF preview와 JSON/reason panel
- 1차 action은 `Open original`, `Open extracted page`, `Open folder`, `Mark ignored`, `Delete`
- 이후 수동 doc_type 지정, vendor store 설치, purchase case 연결 action 추가

### 2. Tax Invoice Parser 개선

현재 확보된 전자세금계산서 원문에는 승인번호와 사업자번호가 있지만 metadata에는 제대로 저장되지 않는다.

필요한 개선:

- `approval_number` 추출
- 공급자/공급받는자 사업자등록번호 추출
- 현재 `document_number`에 사업자번호 일부가 들어가는 문제 수정
- 추출 필드가 안정화되면 validation을 warning에서 hard gate로 올릴지 재검토

주의:

- `전자수정세금계산서`, `전자(세금)계산서`, 공백이 깨진 홈택스 텍스트를 모두 고려해야 한다.
- 지금 당장 `approval_number`를 필수 validation으로 두면 기존 문서가 실패한다.

### 3. OCR/Split 성능 최적화

실제 4페이지 결합 PDF 테스트에서 여러 페이지/타입 조합의 OCR validation이 느릴 수 있다.

개선 후보:

- 페이지별 OCR 결과 cache
- 같은 page/type 조합 중복 validation 제거
- 내장 텍스트가 충분한 경우 Codex image fallback 생략
- split fallback 실행 시간과 attempt 수를 metadata에 기록
- 장시간 OCR은 GUI job log에 진행 상황 출력

### 4. Review용 Audit/Sample Generator

현재 review_required가 비어 있으면 UI 개발과 검증이 어렵다.

필요한 보조 명령:

```bash
python3 scripts/documents/audit_review_required.py --sample
python3 scripts/documents/audit_review_required.py --dry-run
```

역할:

- 기존 `.incoming`과 `purchase`에서 결합 문서, validation 실패 후보를 찾는다.
- 원본은 건드리지 않는다.
- review_required에 복사본과 JSON만 생성할 수 있다.

### 5. GUI Project Mapping

구매 건마다 과제가 다를 수 있으므로 GUI에서 case별 project를 저장/수정할 수 있어야 한다.

권장 저장 위치:

```text
purchase/<case>/.paperworks.yml
```

필드:

```yaml
project_id: "202601800001"
workflow:
  purchase_processed: false
  uploaded: false
notes: ""
```

필요한 UI:

- Dashboard case row에 project 선택 드롭다운
- project 없는 case는 generate/upload 대상에서 제외
- 변경 즉시 `.paperworks.yml` 저장

### 6. GUI Action/Job 통합

장시간 작업은 FastAPI request 안에서 직접 실행하지 않고 job runner로 실행한다.

필요한 action:

- `Collect Docs`
- `Generate Purchase Docs`
- `Upload Purchases`
- `Process Receipts`

필요한 UI:

- Dashboard 상단 action bar
- Jobs 화면에서 stdout/stderr 확인
- 실패 job의 대상 case/project/reason 표시
- action별 skipped case 목록 표시

### 7. Upload Purchases Wrapper

GUI에서 포털 업로드를 안전하게 실행하려면 CLI를 바로 노출하지 말고 wrapper가 필요하다.

권장 파일:

```text
scripts/gui/services/upload_purchase.py
```

역할:

- 선택된 case list와 project mapping 입력
- 각 case에 대해 `preflight` 실행
- 성공한 case만 `fill-save` 실행
- 실패 case는 다음 단계 실행하지 않음
- 부분 성공/실패 결과를 JSON과 job log로 남김

### 8. Settings 화면

React GUI 안에 Settings 화면이 필요하다.

표시할 것:

- `credentials.json` 존재 여부
- Gmail token 존재 여부
- `secret.json` 존재 여부
- `projects.yml` 로딩 상태
- `HOMETAX_PASSWORD` 설정 여부
- GUI/job 작업 디렉터리

표시하지 말 것:

- secret 값
- token 내용
- password/API key

### 9. Codex Safe Diagnosis

실패 job에 대해 Codex가 로그를 보고 원인만 분석하는 safe mode를 붙인다.

단계:

```text
safe:  로그 분석만, 파일 수정 없음
patch: 별도 작업공간에서 수정안 생성
apply: 사용자가 승인한 patch만 적용
```

1차 구현은 `safe`만 연결한다.

금지:

- secret/token/credential 전달
- 임의 shell command 입력
- 자동 patch 적용

### 10. Scheduling

수동 실행이 안정화되면 daily 실행을 systemd timer 또는 cron으로 등록한다.

필요한 것:

- 실행 로그 위치
- 실패 알림 방식
- 중복 실행 방지 lock
- `HOMETAX_PASSWORD`와 Gmail credential 환경 구성

## 테스트 기준

문서 수집 테스트:

```bash
python3 -m unittest discover -s scripts/documents/tests
```

핵심 회귀 테스트는 다음을 포함해야 한다.

- tax invoice validation: `전자세금계산서`, `전자수정세금계산서`, `전자(세금)계산서`
- 결합 PDF split: `tax_invoice + statement`
- 결합 PDF split: `statement + estimate + business_registration + bankbook_copy`
- range merge: 2페이지 견적서
- vendor hard gate: 잘못된 사업자등록증/통장사본이 vendor store에 들어가지 않음
- Gmail processed source skip: 같은 첨부를 재처리하지 않음

GUI 테스트:

- `npm run build`
- `/api/dashboard`
- `/api/files`
- PDF preview
- job log 조회
- path traversal와 secret 파일 차단

업로드 테스트:

- `--step preflight`
- `--step fill-save`
- 여러 `--case-dir` 묶음
- 포털 검증 실패 메시지 감지
- budget/permission failure를 성공으로 오인하지 않음

## 문서 관리 원칙

관리 문서는 루트의 두 파일만 기준으로 한다.

```text
README.md        사용법
development.md   설계 원칙과 남은 구현 항목
```

모듈별 README/development 문서는 만들지 않는다. 세부 구현 설명이 필요하면 코드 주석이나 테스트 이름으로 남기고, 운영자가 알아야 하는 내용은 루트 문서로 올린다.
