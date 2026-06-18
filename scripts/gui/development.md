# Administrative Paperwork GUI Development Notes

이 문서는 현재 저장소의 행정 서류 자동화 작업을 웹 GUI로 확장하기 위한 개발 지침이다. 이전 논의를 모르는 사람이 보더라도 `meeting/`, `purchase/`, 기존 CLI, Codex 개입 범위를 이해하고 구현을 시작할 수 있어야 한다.

## 목표

로컬 웹 앱에서 다음 작업을 처리한다.

- `meeting/` 폴더와 `purchase/` 폴더를 탐색한다.
- 회의비/출장비 영수증, 구매 견적서, 거래명세서, 전자세금계산서, 통장사본, 사업자등록증, 물품 사진을 업로드하고 관리한다.
- 기존 Python CLI를 버튼으로 실행한다.
- 실행 로그와 실패 원인을 웹에서 확인한다.
- 실패한 서류 처리 작업은 필요할 때 Codex CLI가 진단하고, 가능하면 코드 수정까지 수행하게 한다.

초기 구현은 `Streamlit` 로컬 앱을 권장한다. 현재 사용자는 단일 사용자이고, 파일 업로드/목록/버튼/로그 표시가 핵심이므로 Streamlit이 가장 빠르다. 다만 장시간 실행 작업과 Codex repair는 Streamlit 콜백 안에서 직접 처리하지 말고 별도 job runner로 분리한다.

## 현재 저장소 구조

핵심 구조는 다음과 같다.

```text
projects.yml                         # 과제번호/과제명/기본 담당자 설정

meeting/
  receipt/                           # 새 회의비/출장비 영수증 투입 위치
  receipt/used/                      # 처리 완료 후 이동된 영수증
  receipt/ocr_text/                  # OCR 원문
  receipt/records.csv                # 처리 ledger
  receipt/summary.csv                # 회의록 참석자 분산 이력
  output/                            # 생성된 회의록/출장보고서 PDF

purchase/
  <purchase_case>/                   # 구매 건 폴더
    견적.pdf
    거래명세서.pdf
    전자세금계산서.pdf
    통장사본.pdf
    사업자등록증.pdf
    items.xls
    물품검수확인서_작성.pdf
    imgs/
      1.jpg
      2.jpg

scripts/paperwork/
  meeting/                           # 회의록/출장보고서 생성 로직
  purchase/                          # 구매 서류/items.xls/검수확인서 생성 로직
  common/                            # 공통 PDF/OCR/schema 도구

scripts/upload/
  gachon_portal_upload.py            # 가천대 포털 Playwright 업로드 자동화

scripts/gui/
  development.md                     # 이 문서
```

## 권장 GUI 구조

초기 구현은 다음 구조를 권장한다.

```text
scripts/gui/
  app.py                             # Streamlit entrypoint
  services/
    files.py                         # meeting/purchase 파일 탐색, 업로드, rename, trash
    projects.py                      # projects.yml 읽기
    jobs.py                          # subprocess job 실행, 상태/로그 저장
    paperwork.py                     # 기존 CLI 명령 조립
    codex_repair.py                  # Codex 진단/수정 job 생성
  jobs/                              # job 상태와 로그 파일. git ignore 권장
  trash/                             # GUI 삭제 파일 임시 보관. git ignore 권장
```

`scripts/gui/app.py`는 화면과 사용자 입력만 담당한다. 파일 시스템 조작, subprocess 실행, Codex 호출은 `services/` 아래 함수로 분리한다.

## Streamlit으로 가능한 파일 관리

Streamlit 로컬 앱은 파일 관리에 충분하다.

필수 기능:

- 구매 건 폴더 생성: `purchase/<case_name>/`
- 영수증 업로드: `meeting/receipt/`
- 구매 서류 업로드: `purchase/<case_name>/`
- 물품 사진 업로드: `purchase/<case_name>/imgs/`
- 파일 목록 표시
- PDF/이미지 미리보기
- 파일 다운로드
- 파일 이름 변경
- 파일 삭제 대신 `scripts/gui/trash/` 또는 각 case의 `.trash/`로 이동
- 구매 건별 필수 서류 상태 표시

초기에는 업로드할 때 사용자가 파일 종류를 선택하게 한다.

권장 분류:

```text
견적서              -> purchase/<case>/견적.pdf 또는 원본 파일명 유지
거래명세서          -> purchase/<case>/거래명세서.pdf 또는 원본 파일명 유지
전자세금계산서      -> purchase/<case>/전자세금계산서.pdf 또는 원본 파일명 유지
통장사본            -> purchase/<case>/통장사본.pdf 또는 원본 파일명 유지
사업자등록증        -> purchase/<case>/사업자등록증.pdf 또는 원본 파일명 유지
물품사진            -> purchase/<case>/imgs/<original_name>
기타                -> purchase/<case>/<original_name>
```

파일명을 강제로 바꾸면 원본 추적이 어려울 수 있다. 초기 구현은 원본 파일명을 보존하고, 별도 metadata JSON에 사용자가 선택한 분류를 기록하는 방식이 안전하다. 기존 자동화는 PDF 텍스트와 파일명으로 필수 서류를 판별하므로, 나중에 필요하면 표준 파일명으로 복사본을 만들 수 있다.

## 주요 화면

초기 화면은 앱 설명용 landing page가 아니라 실제 작업 화면이어야 한다.

권장 화면:

- `Meeting`
  - `meeting/receipt/` 새 영수증 목록
  - 업로드 버튼
  - 선택 파일에 대해 `process_receipts` 실행
  - `meeting/receipt/records.csv` 상태 표시
  - `meeting/output/` PDF 목록과 미리보기

- `Purchase`
  - 구매 건 목록
  - 새 구매 건 생성
  - 선택 구매 건 파일 목록
  - 서류/사진 업로드
  - 필수 서류 상태 표시
  - `process_purchase` 실행
  - `preflight` 실행
  - 포털 업로드 실행 버튼

- `Jobs`
  - job 목록
  - 실행 상태: `queued`, `running`, `succeeded`, `failed`, `cancelled`
  - stdout/stderr 로그
  - 실패 job에 대한 `Codex 진단` 또는 `Codex 수정 시도` 버튼

- `Settings`
  - `projects.yml` 과제 목록 표시
  - 기본 project 선택
  - OCR/LiteLLM/Codex 사용 가능 여부 점검
  - `secret.json`, `credentials.json` 존재 여부만 표시한다. 내용은 절대 표시하지 않는다.

## 기존 CLI 연결

GUI는 새 서류 처리 로직을 재구현하지 않는다. 기존 CLI를 감싸는 얇은 조작면으로 둔다.

회의비/출장비 처리:

```bash
python3 -m scripts.paperwork.meeting.process_receipts meeting/receipt/<file>
```

여러 파일:

```bash
python3 -m scripts.paperwork.meeting.process_receipts \
  meeting/receipt/a.jpg \
  meeting/receipt/b.pdf
```

구매 서류 생성:

```bash
python3 -m scripts.paperwork.purchase.process_purchase \
  purchase/<case> \
  --project-id <project_id>
```

포털 업로드 전 사전검사:

```bash
python3 scripts/upload/gachon_portal_upload.py \
  --project-id <project_id> \
  --case-dir purchase/<case> \
  --step preflight
```

포털 입력/저장:

```bash
python3 scripts/upload/gachon_portal_upload.py \
  --project-id <project_id> \
  --case-dir purchase/<case> \
  --step fill-save
```

여러 구매 건을 하나의 청구서에 묶을 때:

```bash
python3 scripts/upload/gachon_portal_upload.py \
  --project-id <project_id> \
  --case-dir purchase/<case_a> \
  --case-dir purchase/<case_b> \
  --step preflight
```

## Job Runner 원칙

Streamlit 버튼 콜백에서 장시간 작업을 직접 실행하지 않는다. 별도 job runner를 둔다.

권장 동작:

1. 버튼 클릭 시 job id를 만든다.
2. `scripts/gui/jobs/<job_id>/status.json`을 생성한다.
3. `subprocess.Popen`으로 명령을 실행한다.
4. stdout/stderr를 `stdout.log`, `stderr.log`에 저장한다.
5. Streamlit은 주기적으로 status/log 파일을 읽어 표시한다.

권장 job directory:

```text
scripts/gui/jobs/
  20260618-120301-preflight-260618_waveplate/
    status.json
    command.json
    stdout.log
    stderr.log
```

`status.json` 예시:

```json
{
  "id": "20260618-120301-preflight-260618_waveplate",
  "kind": "preflight",
  "state": "failed",
  "created_at": "2026-06-18T12:03:01Z",
  "started_at": "2026-06-18T12:03:02Z",
  "finished_at": "2026-06-18T12:04:10Z",
  "returncode": 1,
  "case_dirs": ["purchase/260618_waveplate"],
  "project_id": "202500550001"
}
```

## Codex 개입 정책

Codex는 일반 작업 흐름의 첫 번째 처리기가 아니다. 기본 서류 처리는 deterministic Python 코드와 기존 Playwright 자동화가 담당한다. Codex는 실패했을 때 진단/수정 worker로 개입한다.

Codex가 개입해야 하는 경우:

- 새 견적서 양식 때문에 품목 파싱이 실패한다.
- PDF 텍스트 추출/OCR 결과가 기존 파서와 맞지 않는다.
- 필수 서류 판별이 실패한다.
- 포털 selector, iframe, 팝업 흐름이 바뀌어 Playwright 자동화가 막힌다.
- 기존 코드 예외가 발생한다.

Codex가 우선 하지 말아야 하는 일:

- 포털 제출/저장 같은 외부 부작용을 자동으로 반복 실행한다.
- `secret.json`, `credentials.json`, token 파일 내용을 읽거나 prompt에 포함한다.
- 사용자 승인 없이 메인 작업 디렉터리의 코드를 직접 고친다.
- 실패한 파일을 임의 삭제한다.

## Codex 권한 단계

GUI에는 Codex 작업을 단계별로 노출한다.

```text
safe
  실패 로그와 관련 파일 구조를 읽고 원인 분석만 한다.
  코드 수정 없음.

patch
  별도 git worktree에서 코드 수정과 테스트/preflight를 수행한다.
  메인 repo에는 자동 반영하지 않는다.

apply
  사용자가 승인한 patch만 메인 repo에 적용한다.

execute
  사용자가 별도 승인했을 때만 실제 포털 자동화를 실행한다.
```

초기 구현에서는 `safe`와 `patch`까지만 만들어도 충분하다. `apply`와 `execute`는 나중에 추가한다.

## Codex Repair Worker 설계

Codex repair는 Streamlit 서버 프로세스와 분리한다.

입력:

- 실패한 job의 `status.json`
- `stdout.log`, `stderr.log`
- 관련 구매 건 또는 영수증 파일 목록
- 관련 코드 경로
- 사용자가 선택한 권한 단계: `safe` 또는 `patch`

절대 입력하지 않을 것:

- `secret.json` 내용
- `credentials.json` 내용
- OAuth token
- 포털 비밀번호
- API key

`safe` 모드에서 Codex에 줄 수 있는 prompt 개요:

```text
This repository automates administrative paperwork.
Analyze the failed job below.
Do not modify files.
Do not read secret.json, credentials.json, token files, or environment secrets.
Return root cause, likely affected code path, and a proposed fix.
```

`patch` 모드 권장 방식:

1. 현재 repo 상태를 확인한다.
2. 별도 worktree를 만든다.
3. Codex CLI를 해당 worktree에서 실행한다.
4. 테스트 또는 preflight를 실행한다.
5. diff와 결과를 GUI job log에 남긴다.

예시:

```bash
git worktree add /tmp/paperworks-codex-fix-<job_id> -b codex-fix-<job_id>
cd /tmp/paperworks-codex-fix-<job_id>
codex exec --skip-git-repo-check --sandbox workspace-write "<repair prompt>"
```

실제 Codex 실행 옵션은 환경에 맞춰 조정한다. 중요한 점은 메인 repo에서 바로 수정하지 않는 것이다.

## Playwright/Chrome 자동화 주의사항

`scripts/upload/gachon_portal_upload.py`는 이미 Playwright 기반 포털 자동화를 포함한다. GUI는 이 스크립트를 버튼으로 호출한다.

주의:

- 포털 업로드는 외부 시스템에 실제 상태를 만든다.
- `--step preflight`는 안전한 검증 작업으로 GUI에서 적극 사용한다.
- `--step fill-save`는 사용자가 명시적으로 실행해야 한다.
- 포털 자동화가 실패해도 Codex가 자동으로 반복 저장을 시도하면 안 된다.
- headed Chrome이 필요하면 GUI에서 해당 옵션을 노출하되, 기본은 기존 스크립트 기본값을 따른다.
- persistent profile 경로는 기존 기본값 `/tmp/gachon-upload-profile`을 유지하거나 GUI 설정에서 지정한다.

## 컨테이너 전략

초기에는 host에서 Streamlit을 실행하는 것이 가장 단순하다.

```bash
streamlit run scripts/gui/app.py
```

컨테이너는 다음 단계에서 고려한다.

컨테이너 사용 시 주의:

- 저장소를 read-write volume으로 mount한다.
- host UID/GID와 컨테이너 UID/GID를 맞춰 root 소유 파일 생성을 피한다.
- Chrome/Playwright 실행에 필요한 패키지를 이미지에 포함한다.
- `secret.json`, `credentials.json`, token, API key는 이미지에 넣지 않는다.
- Codex 인증과 설정은 별도 volume 또는 host worker 방식으로 분리한다.

권장 장기 구조:

```text
Streamlit/FastAPI GUI container
  - 파일 목록, job 상태, 로그 표시

Host worker or privileged automation worker
  - Playwright/Chrome 포털 자동화
  - Codex repair
  - 실제 파일 수정
```

## 보안과 안전장치

필수 안전장치:

- `secret.json`, `credentials.json`, `token.json`, `.env` 내용은 웹에 표시하지 않는다.
- 삭제는 실제 삭제가 아니라 trash 이동으로 구현한다.
- 파일 덮어쓰기 전에는 기존 파일을 백업하거나 사용자 확인을 받는다.
- job command는 allowlist 방식으로 만든다. 사용자가 임의 shell command를 입력하게 하지 않는다.
- 경로는 반드시 repo root 하위인지 검증한다.
- `..`, absolute path, symlink escape를 막는다.
- Codex repair prompt에는 secret 파일 내용을 넣지 않는다.
- 포털 저장/제출은 사용자가 누른 명시 버튼에서만 실행한다.

## 구현 순서

권장 MVP 순서:

1. `scripts/gui/app.py` Streamlit 앱 생성
2. `projects.yml` 읽기와 project 선택 UI
3. `purchase/` case 목록과 case 생성 UI
4. 구매 case 파일 업로드, 목록, PDF/이미지 미리보기
5. `process_purchase` job 실행
6. `preflight` job 실행
7. `meeting/receipt` 업로드와 `process_receipts` job 실행
8. job 목록과 로그 뷰어
9. 실패 job에 대한 Codex `safe` 진단
10. 별도 worktree를 쓰는 Codex `patch` 모드
11. 포털 `fill-save` 실행 UI
12. Docker/Compose 정리

초기 버전에서 `apply` 자동화는 만들지 않아도 된다. 사람이 diff를 보고 적용하는 흐름이 더 안전하다.

## 완료 기준

MVP 완료 기준:

- 웹에서 구매 건 폴더를 만들 수 있다.
- 웹에서 구매 서류와 물품 사진을 업로드할 수 있다.
- 웹에서 case별 파일 목록과 필수 서류 상태를 볼 수 있다.
- 웹에서 `process_purchase`와 `preflight`를 실행하고 로그를 볼 수 있다.
- 웹에서 회의비/출장비 영수증을 업로드하고 `process_receipts`를 실행할 수 있다.
- 실패 job에 대해 Codex가 원인 분석을 남길 수 있다.
- secret/token 파일 내용이 GUI와 Codex prompt에 노출되지 않는다.

그 다음 단계:

- Codex patch 모드
- 포털 `fill-save` 실행 UI
- Docker/Compose
- 더 정교한 PDF/Excel 미리보기
- 자동 서류 분류
