# Gachon Portal Upload Automation

가천대 포털 연구ㆍ산학 시스템에서 구매 건의 `일반청구` 입력, 서류 업로드, 구매내역 엑셀 등록, 임시저장, 필요 시 신청 버튼과 결재선 팝업 확인까지 자동화하는 Playwright 스크립트다.

스크립트는 보안을 위해 산학 시스템의 상세 내부 URL을 직접 열지 않는다. 포털 로그인 후 `/p/T09/`로 들어가고, 이후에는 사용자가 보는 화면의 탭ㆍ버튼ㆍ팝업을 따라 이동한다.

## 할 수 있는 일

- 포털 로그인 및 연구ㆍ산학 시스템 진입
- `projects.yml`의 과제번호/과제명으로 대상 과제 선택
- `청구서` 탭의 `일반청구` 작성
- 예산 `연구재료비`, 세목 `연구재료 구입비/...`, 영수증 `세금계산서` 선택
- 계산서수신메일 검색 후 `items.xls` 총액과 일치하는 전자세금계산서 선택
- 전자세금계산서 공급자 기준 지급처 선택
- RCMS 과제에서 거래처 마스터의 누락 정보 보정
- 적요, 검수일자, 검수자 입력
- 필수 첨부문서 업로드
- 부가증빙 구매내역 `items.xls` 등록
- 여러 구매 폴더를 하나의 청구서에 청구내역 여러 줄로 묶기
- 임시저장 건을 다시 열어 추가 입력 또는 신청
- 신청 후 결재선 팝업에서 기본 담당자 행 선택 및 확인

## 아직 주의할 점

자동화가 `신청` 버튼과 결재선 팝업까지 처리하더라도 포털 서버 검증은 통과해야 한다. 예산 초과, 권한 부족, 필수 입력 누락, 거래처 정보 오류가 나오면 신청 완료로 보지 않고 중단한다.

`2026-06-18` RCMS 테스트에서는 결재선 팝업까지 자동 처리됐지만, 포털이 `미징수액 1,321,200원`, `청구액 2,948,000원`, `해당비목이 예산액을 초과하였습니다.` 메시지를 반환해 최종 신청은 차단됐다.

## 설치

저장소 루트에서 실행한다.

```bash
python3 -m pip install --user playwright
python3 scripts/upload/gachon_portal_upload.py --help
```

스크립트는 시스템 Chrome을 사용하는 Python Playwright 구현이다. 실행 기준 파일은 `gachon_portal_upload.py`다.

## 필요한 설정 파일

### `secret.json`

기본 로그인 정보 파일이다. 저장소 루트에 둔다.

```json
{
  "id": "포털아이디",
  "pwd": "포털비밀번호"
}
```

다른 경로를 쓰려면 `--secret`을 지정한다.

```bash
python3 scripts/upload/gachon_portal_upload.py \
  --secret /path/to/secret.json \
  --interactive
```

`secret.json`과 비슷한 인증 파일은 절대 커밋하지 않는다.

### `projects.yml`

과제 정보는 저장소 루트의 `projects.yml` 한 파일에서 관리한다. `principal_investigator`, `inspector`는 공통 기본값이고, 각 과제는 번호와 과제명만 둔다.

```yaml
defaults:
  principal_investigator: "양대호"
  inspector: "양대호"

projects:
  "202601800001":
    no: "202601800001"
    name: "(RCMS)기존 리튬이온 배터리 대비 100배 빠른 초고속 충전 양자배터리 핵심기술"
```

업로드할 때는 `--project-id`로 여기 있는 키 또는 과제번호를 지정한다. 생략하면 스크립트 기본 과제번호가 사용된다.

## 구매 폴더 준비

각 구매 건은 하나의 `case-dir`로 처리한다. 예:

```text
purchase/260618_pmmfa/
  items.xls
  물품검수확인서_작성.pdf
  전세.pdf
  견적.pdf
  거명.pdf
  통장사본.pdf
  사업자등록증.pdf
```

필수 파일은 다음 6종이다.

- `물품검수확인서`
- `전자세금계산서`
- `견적서`
- `거래명세서`
- `통장사본`
- `사업자등록증`

파일명은 반드시 위 이름 그대로일 필요는 없다. 스크립트는 PDF 텍스트도 읽어서 문서 종류를 판별한다. 예를 들어 숫자로 된 병합 PDF 하나에 전자세금계산서, 견적서, 거래명세서가 함께 들어 있어도 각 문서로 인식될 수 있다. 실제 업로드할 때는 같은 PDF가 여러 문서 조건을 만족해도 중복 업로드하지 않는다.

`items.xls`는 반드시 있어야 하며, 첫 번째 시트에 `품명`, `수량`, `총구입액` 컬럼이 있어야 한다. `총구입액` 합계는 전자세금계산서 검색 결과의 총액과 일치해야 한다.

## 물품검수확인서 생성

업로드 전에 구매 폴더의 서류 처리를 먼저 끝낸다. 예:

```bash
python3 -m scripts.paperwork.purchase.process_purchase \
  purchase/260618_pmmfa \
  --project-id 202601800001
```

물품검수확인서의 과제/검수자 정보는 `projects.yml`의 과제 정보와 공통 기본값을 사용한다.

## 기본 실행 순서

처음에는 바로 저장하지 말고 사전검사를 먼저 돌린다.

```bash
python3 scripts/upload/gachon_portal_upload.py \
  --project-id 202601800001 \
  --case-dir purchase/260618_pmmfa \
  --step preflight
```

문서와 `items.xls`가 통과하면 임시저장까지 실행한다.

```bash
python3 scripts/upload/gachon_portal_upload.py \
  --project-id 202601800001 \
  --case-dir purchase/260618_pmmfa \
  --step fill-save
```

임시저장 건을 신청하려면 목록에 보이는 현재 금액을 `--draft-total`로 지정한다.

```bash
python3 scripts/upload/gachon_portal_upload.py \
  --project-id 202601800001 \
  --draft-total 2948000 \
  --step submit-draft
```

저장 후 바로 신청까지 시도하려면 `fill-submit`을 사용한다.

```bash
python3 scripts/upload/gachon_portal_upload.py \
  --project-id 202601800001 \
  --case-dir purchase/260618_pmmfa \
  --step fill-submit
```

## 여러 구매 건 묶기

여러 구매 폴더를 하나의 청구서에 청구내역 여러 줄로 묶을 때는 `--case-dir`를 반복한다. 스크립트는 하위 폴더를 자동 순회하지 않고, 명시한 폴더만 지정한 순서대로 처리한다.

```bash
python3 scripts/upload/gachon_portal_upload.py \
  --project-id 202601800001 \
  --case-dir purchase/260618_pmmfa \
  --case-dir purchase/260618_waveplate \
  --case-dir purchase/260406_optics/1번 \
  --case-dir purchase/260406_optics/2번 \
  --case-dir purchase/260406_optics/3번 \
  --step fill-save
```

이미 만들어진 임시저장 건에 추가 청구내역을 붙이려면 현재 임시저장 건의 합계 금액을 `--draft-total`로 지정하고 `append-draft`를 사용한다.

```bash
python3 scripts/upload/gachon_portal_upload.py \
  --project-id 202601800001 \
  --draft-total 4548500 \
  --case-dir purchase/260406_optics/1번 \
  --case-dir purchase/260406_optics/2번 \
  --case-dir purchase/260406_optics/3번 \
  --step append-draft
```

저장 후 포털이 `내역을 추가하시겠습니까?`를 묻는 경우 기본값은 `확인`이다. 이 동작이 있어야 현재 청구내역이 반영되고 다음 청구내역 입력 상태로 넘어간다.

## 자주 쓰는 명령

브라우저 창 없이 한 단계만 실행:

```bash
python3 scripts/upload/gachon_portal_upload.py --step list-actions
```

브라우저 창을 보면서 실행:

```bash
python3 scripts/upload/gachon_portal_upload.py --interactive --headed
```

세션을 유지하면서 단계별 테스트:

```bash
python3 scripts/upload/gachon_portal_upload.py \
  --project-id 202601800001 \
  --interactive
```

interactive에서 사용할 수 있는 주요 명령:

```text
login
t09
project
claim
general
basic
invoice
mail
select-invoice
kind
payee
detail
inspect
attach
evid
items
save
fill-save
fill-submit
append-draft
preflight
prepare
dump
actions
list-actions
vendor-dump
vendor-update
submit-actions
submit-draft
quit
```

기본 persistent profile은 `/tmp/gachon-upload-profile`이다. 같은 profile을 재사용하면 로그인 세션이 유지될 수 있다. 다른 세션을 분리하고 싶으면 `--profile`을 지정한다.

```bash
python3 scripts/upload/gachon_portal_upload.py \
  --profile /tmp/gachon-upload-profile-test \
  --interactive --headed
```

## 옵션 요약

- `--project-id`: `projects.yml`의 과제 키 또는 과제번호
- `--project-no`: 과제번호 직접 지정
- `--project-name`: 과제명 직접 지정
- `--case-dir`: 구매 폴더. 여러 번 지정 가능
- `--draft-total`: 기존 임시저장 건을 열 때 사용할 금액
- `--summary`: 적요 직접 지정
- `--invoice-mail`: 전자세금계산서 수신메일 검색어. 기본값은 `sheepvs5@gmail.com`
- `--secret`: 로그인 정보 파일
- `--projects-yml`: 과제 정보 파일
- `--profile`: Playwright persistent profile 경로
- `--headed`: 브라우저 창 표시
- `--interactive`: 단계별 명령 모드
- `--step`: 한 단계 실행

`--summary`를 생략하면 `items.xls`의 첫 번째 품목명으로 적요를 만든다. 품목이 1개이면 `품목명`, 2개 이상이면 `품목명 외 N` 형식이다.

## 자동 검증

업로드 전에 지정된 모든 `--case-dir`에 대해 다음을 먼저 확인한다.

- 구매 폴더가 존재하는지
- `items.xls`가 있는지
- `items.xls`에 품목이 있는지
- `총구입액` 합계가 0이 아닌지
- 필수 서류 6종이 모두 있는지
- 전자세금계산서에서 공급자 후보를 읽을 수 있는지

업로드 중에는 다음을 확인한다.

- 계산서 검색 결과 중 `items.xls` 총액과 일치하는 행만 선택
- 지급처가 전자세금계산서 공급자 후보와 일치하는지 확인
- 저장 후 포털 검증 팝업에 실패 메시지가 있는지 확인
- 신청 후 포털 검증 팝업에 실패 메시지가 있는지 확인

다음 메시지가 나오면 실패로 보고 중단한다.

- `확인하세요`
- `입력하세요`
- `선택하세요`
- `등록되지`
- `오류`
- `실패`
- `권한`
- `예산액 초과`
- `미징수액`
- `잔액`

## 지급처와 거래처 정보

지급처는 하드코딩하지 않는다. 전자세금계산서 PDF의 공급자 상호/사업자번호와 선택한 계산서 행 텍스트에서 후보 토큰을 만들고, 지급처 팝업의 행과 대조한다.

RCMS 과제에서 지급처의 주소, 대표자, 연락처 같은 필수 정보가 없어 선택이 거절되면 스크립트가 사업자등록증, 거래명세서, 전자세금계산서 텍스트에서 다음 정보를 추출해 거래처 마스터 보정을 시도한다.

- 사업자등록번호
- 상호
- 대표자
- 주소
- 우편번호
- 전화번호
- 이메일
- 은행명
- 계좌번호
- 예금주

이미 등록되어 있고 선택이 정상적으로 되면 굳이 모든 값을 등록하지 않는다. 거래처 선택이 막히거나 마스터 보정 명령을 실행할 때만 문서 텍스트를 이용해 보정한다.

## RCMS 과제 처리

RCMS 과제에서 추가 필수 선택값이 비어 있으면 스크립트가 보이는 필수 select의 첫 유효 옵션을 선택한다. 현재 확인된 기본값은 `사용금액구분=본예산`이다.

이 값은 포털 화면 구조에 의존하므로, 다른 RCMS 과제에서 서버 검증 메시지가 나오면 `--interactive --headed`로 화면을 보며 확인한다.

## 트러블슈팅

T09 진입이 한두 번 실패할 수 있다. SSO가 일시적으로 포털 루트나 장애 화면을 반환하는 경우가 있어 스크립트가 포털 루트부터 재시도한다.

팝업이 버튼 클릭을 막으면 `--headed`로 실행해서 화면에 남은 안내 팝업을 확인한다. 알려진 팝업은 자동으로 닫지만, 새 안내문은 추가 처리가 필요할 수 있다.

전자세금계산서가 선택되지 않으면 `items.xls`의 `총구입액` 합계와 세금계산서 총액이 같은지 확인한다. 스크립트는 첫 행을 무조건 고르지 않는다.

필수 서류가 없다고 나오면 `pdftotext`로 PDF 텍스트가 읽히는지 확인한다. 스캔본처럼 텍스트가 없는 PDF는 파일명 힌트가 중요하다.

임시저장 건을 다시 열 수 없으면 `--draft-total` 금액이 목록에 보이는 금액과 정확히 일치하는지 확인한다. 콤마는 넣지 않아도 된다.

신청이 실패했는데 결재선 팝업은 처리된 경우, 자동화 문제가 아니라 포털 서버 검증일 수 있다. 목록에서 진행구분이 계속 `임시저장`이면 신청 완료가 아니다.

## 확인된 테스트 기록

`purchase/260618_pmmfa`

- 과제: `202601800001`
- 금액: `2,948,000`
- 결과: 일반청구 임시저장 생성
- 신청 버튼과 결재선 팝업 처리 확인
- 최종 신청은 연구재료비 예산 초과 메시지로 차단

`purchase/260618_waveplate`

- 금액: `1,600,500`
- 결과: 일반청구 임시저장 생성
- 적요: `WPH05M-808`

`purchase/260406_optics/1번`, `2번`, `3번`

- 병합 PDF 기반 필수 서류 판별 확인
- 여러 `--case-dir`를 하나의 청구서에 묶는 흐름 확인

## 개발 메모

- 산학 시스템은 iframe과 팝업을 많이 사용한다. 선택자는 현재 page가 아니라 모든 page/frame에서 찾아야 한다.
- 같은 `id`가 DOM에 여러 번 나오거나 hidden 요소가 먼저 나올 수 있다. visible element 기준으로 처리한다.
- select option은 상위 선택 후 비동기로 갱신된다. 즉시 다음 값을 넣으면 실패할 수 있다.
- 저장 후 `내역을 추가하시겠습니까?`에서 취소를 누르면 현재 입력이 반영되지 않을 수 있다.
- 실패 원인을 코드에서 제거하지 않는다. 로그인 재시도, 팝업 탐색, 서버 검증 메시지 처리는 운영 중에도 필요한 안정화 로직이다.
