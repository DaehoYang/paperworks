# Paperworks GUI Development Plan

이 문서는 앞으로 개발할 GUI 통합 방향을 정리한다. 이미 구현된 파일브라우저 자체를 설명하는 문서가 아니라, `scripts/gui`, `scripts/documents`, `scripts/paperwork`, `scripts/upload`를 하나의 행정처리 도구로 묶기 위한 작업 기준이다.

## 목표

GUI는 단순 파일 탐색기가 아니라 행정처리 작업대가 되어야 한다.

- `scripts/documents`: Gmail/문서 수집, 구매 폴더 자동 생성, 누락 서류 보강
- `scripts/paperwork`: 회의/출장/구매 제출 서류 생성
- `scripts/upload`: 가천대 포털 업로드 자동화
- `scripts/gui`: 위 기능을 상태관리, 파일관리, 일괄 실행, 로그 확인으로 묶는 웹 UI

현재 React/FastAPI GUI는 파일브라우저와 기본 Dashboard가 있다. 다음 개발은 “상단 일괄 실행 버튼”과 “구매 건별 프로젝트 선택/상태관리”가 중심이다.

## 핵심 화면 구조

45001의 React/FastAPI 앱을 기준으로 한다.

```text
Dashboard
  - 전체 상태 요약
  - Missing Documents 기본 표시
  - Show all 버튼으로 전체 구매 건 표시
  - 상단 일괄 실행 버튼

File Browser
  - purchase/와 meeting/ 파일 탐색
  - drag/drop upload
  - preview, rename, move/copy, trash delete

Jobs
  - 실행 중/완료/실패 job 목록
  - stdout/stderr 로그
  - 실패 job에 대한 Codex 진단/수정

Settings
  - projects.yml 과제 목록
  - 기본값
  - secret/credentials 존재 여부만 표시
```

Dashboard와 File Browser는 이미 같은 React 앱에 있어야 한다. 다음에는 Jobs와 Settings를 React 앱 안에 추가한다. 예전 Streamlit 구현은 참고용으로 남길 수 있지만, 주 개발 대상은 React/FastAPI다.

## 상단 일괄 실행 버튼

상단에는 작업 버튼을 항상 보이게 둔다. 버튼은 개별 파일 작업이 아니라 현재 선택된 대상 또는 전체 대상에 대해 실행한다.

권장 버튼:

```text
[Collect Documents] [Process Purchases] [Upload Purchases] [Process Receipts] [Jobs]
```

한글 UI를 쓴다면:

```text
[문서 수집] [구매서류 생성] [구매 업로드] [영수증 처리] [작업 로그]
```

각 버튼의 의미:

- `Collect Documents`
  - `scripts/documents/run_daily.py` 실행
  - Gmail에서 전자세금계산서/견적서/거래명세서/사업자등록증/통장사본을 수집
  - `purchase/<YYMMDD>_<업체명>/` 폴더와 `purchase/documents.sqlite3` 갱신

- `Process Purchases`
  - 선택된 구매 건 또는 처리 가능한 전체 구매 건에 대해 `process_purchase` 일괄 실행
  - 내부 명령:
    ```bash
    python3 -m scripts.paperwork.purchase.process_purchase purchase/<case> --project-id <project_id>
    ```
  - 출력:
    - `items.xls`
    - `물품검수확인서_작성.pdf`

- `Upload Purchases`
  - 선택된 구매 건 또는 업로드 가능한 전체 구매 건을 포털에 업로드
  - UI 이름은 `upload_purchase` 또는 `구매 업로드`
  - 내부적으로는 `preflight`를 먼저 자동 실행하고 성공한 건만 `fill-save` 실행
  - 사용자에게 `fill-save`라는 내부 단계명은 노출하지 않는다.

- `Process Receipts`
  - `meeting/receipt/`의 선택 영수증 또는 미처리 영수증 전체 처리
  - 내부 명령:
    ```bash
    python3 -m scripts.paperwork.meeting.process_receipts meeting/receipt/<file>
    ```

- `Jobs`
  - job 목록/로그 화면으로 이동

## preflight와 upload_purchase

`preflight`는 독립 버튼으로 노출하지 않는다. `Upload Purchases`의 내부 단계로 사용한다.

흐름:

```text
Upload Purchases 클릭
  1. 선택 구매 건 목록 계산
  2. 각 구매 건의 project_id 확인
  3. preflight 실행
  4. preflight 성공 건만 fill-save 실행
  5. 실패 건은 업로드하지 않고 로그와 실패 이유 표시
```

내부 명령:

```bash
python3 scripts/upload/gachon_portal_upload.py \
  --project-id <project_id> \
  --case-dir purchase/<case> \
  --step preflight

python3 scripts/upload/gachon_portal_upload.py \
  --project-id <project_id> \
  --case-dir purchase/<case> \
  --step fill-save
```

GUI job 이름:

```text
upload_purchase
```

CLI 내부 호환성:

```text
preflight
fill-save
```

`fill-save` 이름은 기존 CLI 내부 단계명으로 유지한다. GUI, job kind, log title에는 `upload_purchase`를 사용한다.

## 구매 건별 프로젝트 선택

중요한 요구사항: 구매 건마다 과제가 다를 수 있다.

따라서 “전체 구매 업로드”를 하려면 구매 건별 `project_id`를 저장하고 확인할 수 있어야 한다.

권장 데이터 위치:

```text
purchase/<case>/.paperworks.yml
```

예시:

```yaml
project_id: "202500550001"
upload_group: ""
status:
  documents_collected: true
  purchase_processed: false
  upload_ready: false
  uploaded: false
notes: ""
```

GUI 동작:

- Dashboard의 구매 건 목록에 project 선택 드롭다운을 둔다.
- 기본값은 `projects.yml`에서 선택한 기본 과제 또는 이전에 저장된 `.paperworks.yml` 값이다.
- 사용자가 구매 건별 project를 변경하면 즉시 `.paperworks.yml`에 저장한다.
- `Process Purchases`와 `Upload Purchases`는 각 구매 건의 `.paperworks.yml.project_id`를 사용한다.
- project가 없는 구매 건은 일괄 실행 대상에서 제외하고 “project 필요” 상태로 표시한다.

일괄 처리 대상 계산:

```text
Process Purchases:
  - project_id 있음
  - 견적서 있음
  - 이미지 폴더/imgs 또는 사진 있음
  - 아직 items.xls 또는 물품검수확인서_작성.pdf가 없거나 사용자가 재실행 선택

Upload Purchases:
  - project_id 있음
  - process_purchase 산출물 있음
  - 필수 첨부 문서 있음
  - preflight 통과
```

## documents 통합

`scripts/documents`는 GUI에서 별도 탭/버튼으로 통합한다.

주요 작업:

- `Collect Documents` 버튼:
  ```bash
  HOMETAX_PASSWORD=<secret> python3 scripts/documents/run_daily.py
  ```

- dry run 버튼 또는 옵션:
  ```bash
  python3 scripts/documents/run_daily.py --newer-than 2m --dry-run --no-labels
  ```

- 구매 폴더 체크:
  ```bash
  python3 scripts/documents/check_purchase_docs.py purchase/<case> --format json
  ```

- 후보 추천:
  ```bash
  python3 scripts/documents/suggest_purchase_docs.py purchase/<case>
  ```

GUI 표시:

- documents DB 상태: `purchase/documents.sqlite3`
- 최근 수집 결과
- incomplete/ready/finished case 수
- 업체별 vendors 문서 보유 상태
- 각 구매 건에 자동 보강 가능한 후보

주의:

- `HOMETAX_PASSWORD`, Gmail token, credentials 내용은 GUI에 표시하지 않는다.
- GUI는 secret 값 존재 여부만 표시한다.
- run_daily 실행 결과는 job log로만 남긴다.

## Job Runner

모든 장시간 작업은 FastAPI request 안에서 직접 실행하지 않는다. 기존 `scripts/gui/services/jobs.py`와 `job_worker.py` 구조를 React backend에서 재사용한다.

필요한 API:

```text
GET  /api/jobs
GET  /api/jobs/{job_id}
GET  /api/jobs/{job_id}/stdout
GET  /api/jobs/{job_id}/stderr
POST /api/actions/collect_documents
POST /api/actions/process_purchases
POST /api/actions/upload_purchases
POST /api/actions/process_receipts
POST /api/actions/codex_safe_analysis
```

job metadata에는 반드시 대상과 project를 남긴다.

```json
{
  "kind": "upload_purchase",
  "case_dirs": ["purchase/260618_에이이노텍"],
  "project_id": "202500550001",
  "phase": "preflight"
}
```

`upload_purchase`는 단일 shell command보다 Python wrapper가 낫다. wrapper가 각 case에 대해 `preflight -> fill-save`를 순서대로 실행하고, 실패 시 다음 동작 정책을 결정한다.

권장 wrapper:

```text
scripts/gui/services/upload_purchase.py
```

역할:

- case list와 project mapping 입력
- case별 preflight 실행
- 성공한 case만 fill-save 실행
- 전체 결과 JSON 출력
- 부분 실패를 job log에 명확히 기록

## Codex Repair

Codex는 일반 실행 버튼이 아니라 실패 복구 기능이다.

권장 단계:

```text
safe
  실패 로그 분석만 수행
  파일 수정 없음

patch
  별도 git worktree 또는 임시 작업공간에서 코드 수정
  테스트/preflight 재실행
  메인 repo에는 자동 적용하지 않음

apply
  사용자가 승인한 patch만 적용
```

React GUI에서는 우선 `safe`만 연결한다.

Codex에 넘기지 않을 것:

- `secret.json`
- `credentials.json`
- `token.json`
- `.env`
- 포털 비밀번호
- API key

## 보안 모델

파일 API:

- 노출 root는 `purchase/`, `meeting/`만
- repo root 밖 path traversal 차단
- symlink escape 차단
- dotfile, token, credentials, secret 차단
- 삭제는 실제 삭제가 아니라 `scripts/gui/trash/` 이동
- upload 확장자 allowlist 적용

명령 실행:

- frontend에서 arbitrary command를 받지 않는다.
- backend action은 allowlist API만 제공한다.
- shell string 조립 대신 list command를 사용한다.
- 포털에 실제 저장하는 `upload_purchase`는 확인 단계가 필요하다.

권장 확인:

```text
Upload Purchases 클릭
  -> 대상 case/project 요약 표시
  -> "preflight 후 포털 저장을 실행" 확인
  -> job 시작
```

## 우선순위

1. 구매 건별 `.paperworks.yml` project mapping
2. React Dashboard에서 project 선택 UI
3. React backend action API와 job list/log API
4. `Process Purchases` 일괄 실행
5. `Upload Purchases` wrapper: preflight 자동 실행 후 fill-save
6. `Process Receipts` 일괄 실행
7. `Collect Documents` 실행과 documents DB 상태 표시
8. 실패 job에 대한 Codex safe analysis
9. 필요 시 patch 모드

## 완료 기준

다음이 되면 GUI 통합 1차 완료로 본다.

- 45001에서 Dashboard, File Browser, Jobs, Settings가 모두 보인다.
- Dashboard 상단 버튼으로 문서 수집, 구매서류 생성, 구매 업로드, 영수증 처리를 실행할 수 있다.
- 구매 건마다 project를 선택/저장할 수 있다.
- 전체 실행 버튼은 project가 없는 구매 건을 건너뛰고 이유를 표시한다.
- `upload_purchase`는 preflight 실패 시 포털 저장을 하지 않는다.
- 모든 실행은 job log로 추적된다.
- 실패 job에서 Codex safe 진단을 실행할 수 있다.
- secret/token/credentials 내용은 UI, log, Codex prompt에 노출되지 않는다.
