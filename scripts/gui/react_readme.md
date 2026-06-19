# React/FastAPI GUI

Streamlit Explorer보다 강한 파일브라우저가 필요할 때 쓰는 React 기반 GUI다.

구성:

```text
scripts/gui/react_backend/   # FastAPI file API and static frontend serving
scripts/gui/frontend/        # Vite + React + @cubone/react-file-manager
scripts/gui/run_react.py     # Jupyter server proxy friendly runner
```

초기 설치/빌드:

```bash
cd scripts/gui/frontend
npm install
npm run build
```

실행:

```bash
python3 scripts/gui/run_react.py --port 45001 --detach
```

접속:

```text
https://dhlab.gachon.ac.kr/user/sheepvs5/proxy/45001/
```

노출되는 root는 `purchase/`와 `meeting/`뿐이다. `secret.json`, `credentials.json`, token 파일, dotfile, 실행 스크립트류는 backend에서 차단한다. 삭제는 실제 삭제가 아니라 `scripts/gui/trash/`로 이동한다.

지원 기능:

- Explorer형 list/grid view
- breadcrumb/sidebar navigation
- drag-and-drop upload
- drag-and-drop move/copy
- multi-select
- context menu
- rename
- download
- preview
- keyboard shortcuts
