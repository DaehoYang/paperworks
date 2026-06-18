from __future__ import annotations

import base64
import shutil
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from scripts.gui.services import codex_repair, files, jobs, paperwork, projects
from scripts.gui.services.paths import MEETING_DIR, PROJECTS_YML, PURCHASE_DIR, ROOT_DIR, ensure_gui_dirs, resolve_repo_path


DOC_TYPES = ["기타", "견적서", "거래명세서", "전자세금계산서", "통장사본", "사업자등록증", "물품검수확인서", "물품사진"]
PARSE_ENGINES = ["auto", "pdf-text", "ocr-litellm", "codex"]


def init_page() -> None:
    ensure_gui_dirs()
    st.set_page_config(page_title="Paperworks", page_icon="📄", layout="wide")
    st.markdown(
        """
        <style>
        .block-container { padding-top: 1.25rem; padding-bottom: 2rem; }
        div[data-testid="stMetric"] { border: 1px solid #e5e7eb; padding: 0.6rem 0.8rem; border-radius: 6px; }
        .small-muted { color: #6b7280; font-size: 0.86rem; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def selected_project() -> projects.Project | None:
    options = projects.project_options()
    if not options:
        st.sidebar.warning("projects.yml에 과제 정보가 없습니다.")
        return None
    labels = list(options)
    current = st.session_state.get("project_label")
    index = labels.index(current) if current in labels else 0
    label = st.sidebar.selectbox("과제", labels, index=index)
    st.session_state["project_label"] = label
    return options[label]


def show_job_created(job: jobs.Job) -> None:
    st.success(f"작업을 시작했습니다: {job.id}")


def file_table(infos: list[files.FileInfo]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "파일": info.name,
                "경로": info.rel_path,
                "크기": files.human_size(info.size),
                "수정일": info.modified,
            }
            for info in infos
        ]
    )


def preview_file(path: Path) -> None:
    info = files.file_info(path)
    st.caption(info.rel_path)
    with path.open("rb") as handle:
        data = handle.read()
    st.download_button("다운로드", data=data, file_name=path.name, key=f"download-{info.rel_path}")
    if info.is_image:
        st.image(data, caption=path.name, use_container_width=True)
    elif info.is_pdf:
        encoded = base64.b64encode(data).decode("ascii")
        st.markdown(
            f'<iframe src="data:application/pdf;base64,{encoded}" width="100%" height="720"></iframe>',
            unsafe_allow_html=True,
        )
    elif info.suffix in files.PREVIEW_TEXT_EXTENSIONS:
        st.code(data.decode("utf-8", errors="replace")[:20000])
    else:
        st.info("이 파일 형식은 미리보기를 제공하지 않습니다.")


def file_management_panel(infos: list[files.FileInfo], key_prefix: str) -> None:
    if not infos:
        st.info("표시할 파일이 없습니다.")
        return
    st.dataframe(file_table(infos), use_container_width=True, hide_index=True)
    path_by_label = {f"{info.rel_path}": info.path for info in infos}
    selected = st.selectbox("파일 선택", list(path_by_label), key=f"{key_prefix}-file-select")
    path = path_by_label[selected]
    left, right = st.columns([3, 2])
    with left:
        preview_file(path)
    with right:
        st.subheader("파일 작업")
        new_name = st.text_input("새 파일명", value=path.name, key=f"{key_prefix}-rename-input")
        if st.button("이름 변경", key=f"{key_prefix}-rename"):
            try:
                target = files.rename_file(path, new_name)
                st.success(f"변경됨: {target.name}")
                st.rerun()
            except Exception as exc:
                st.error(str(exc))
        st.divider()
        confirm = st.checkbox("선택 파일을 trash로 이동", key=f"{key_prefix}-trash-confirm")
        if st.button("Trash로 이동", disabled=not confirm, key=f"{key_prefix}-trash"):
            try:
                target = files.trash_file(path)
                st.warning(f"이동됨: {target}")
                st.rerun()
            except Exception as exc:
                st.error(str(exc))


def purchase_page(project: projects.Project | None) -> None:
    st.title("Purchase")
    left, right = st.columns([1, 2])
    with left:
        st.subheader("구매 건")
        case_paths = files.list_purchase_cases()
        case_names = [path.name for path in case_paths]
        with st.form("create-case"):
            new_case = st.text_input("새 구매 건 폴더명", placeholder="260618_waveplate")
            created = st.form_submit_button("폴더 생성")
        if created:
            try:
                case_dir = files.create_purchase_case(new_case)
                st.success(f"생성됨: {case_dir.relative_to(ROOT_DIR)}")
                st.rerun()
            except Exception as exc:
                st.error(str(exc))
        if not case_names:
            st.info("purchase 폴더에 구매 건이 없습니다.")
            return
        selected_case_names = st.multiselect("작업할 구매 건", case_names, default=[case_names[-1]])
        primary_name = selected_case_names[0] if selected_case_names else case_names[-1]
        primary_case = PURCHASE_DIR / primary_name
    with right:
        st.subheader(primary_name)
        status = files.required_purchase_status(primary_case)
        cols = st.columns(3)
        for index, (label, matches) in enumerate(status.items()):
            cols[index % 3].metric(label, "OK" if matches else "누락", delta=None)
        with st.expander("필수 서류 감지 상세", expanded=False):
            for label, matches in status.items():
                st.write(f"**{label}**")
                if matches:
                    st.write("\n".join(f"- `{item}`" for item in matches))
                else:
                    st.caption("파일명 기준 감지 없음. 최종 판단은 preflight가 수행합니다.")

    st.divider()
    upload_col, action_col = st.columns([1, 1])
    with upload_col:
        st.subheader("업로드")
        doc_type = st.selectbox("파일 종류", DOC_TYPES, index=0)
        uploaded = st.file_uploader(
            "구매 서류 또는 사진",
            accept_multiple_files=True,
            type=[ext.lstrip(".") for ext in sorted(files.UPLOAD_EXTENSIONS)],
            key="purchase-uploader",
        )
        if st.button("선택 구매 건에 저장", disabled=not uploaded):
            try:
                saved = files.save_purchase_uploads(primary_case, uploaded or [], doc_type)
                st.success(f"{len(saved)}개 파일 저장")
                st.rerun()
            except Exception as exc:
                st.error(str(exc))
    with action_col:
        st.subheader("작업 실행")
        parse_engine = st.selectbox("견적서 파서", PARSE_ENGINES, index=0)
        inspection_date = st.date_input("검수일")
        project_id = project.key if project else None
        selected_case_dirs = [PURCHASE_DIR / name for name in selected_case_names]
        if st.button("물품검수확인서/items.xls 생성", disabled=not selected_case_dirs):
            job = jobs.start_job(
                "process-purchase",
                paperwork.process_purchase_command(
                    primary_case,
                    project_id=project_id,
                    parse_engine=parse_engine,
                    inspection_date=inspection_date.isoformat(),
                ),
                metadata={"case_dirs": [files.repo_relative(primary_case)], "project_id": project_id or ""},
                cwd=paperwork.command_cwd(),
            )
            show_job_created(job)
        if st.button("Preflight 검사", disabled=not selected_case_dirs):
            job = jobs.start_job(
                "portal-preflight",
                paperwork.portal_command(selected_case_dirs, project_id=project_id, step="preflight"),
                metadata={"case_dirs": [files.repo_relative(path) for path in selected_case_dirs], "project_id": project_id or ""},
                cwd=paperwork.command_cwd(),
            )
            show_job_created(job)
        headed = st.checkbox("Chrome 창 표시", value=False)
        confirm_upload = st.checkbox("포털 입력/저장을 실행할 것을 확인")
        if st.button("포털 입력/저장 실행", disabled=not selected_case_dirs or not confirm_upload):
            job = jobs.start_job(
                "portal-fill-save",
                paperwork.portal_command(selected_case_dirs, project_id=project_id, step="fill-save", headed=headed),
                metadata={"case_dirs": [files.repo_relative(path) for path in selected_case_dirs], "project_id": project_id or ""},
                cwd=paperwork.command_cwd(),
            )
            show_job_created(job)

    st.divider()
    st.subheader("파일")
    file_management_panel(files.list_files(primary_case), "purchase")


def meeting_page() -> None:
    st.title("Meeting")
    receipt_dir = MEETING_DIR / "receipt"
    output_dir = MEETING_DIR / "output"
    left, right = st.columns([1, 1])
    with left:
        st.subheader("영수증 업로드")
        uploaded = st.file_uploader(
            "회의비/출장비 영수증",
            accept_multiple_files=True,
            type=[ext.lstrip(".") for ext in sorted(files.UPLOAD_EXTENSIONS)],
            key="meeting-uploader",
        )
        if st.button("meeting/receipt에 저장", disabled=not uploaded):
            try:
                saved = files.save_meeting_receipts(uploaded or [])
                st.success(f"{len(saved)}개 파일 저장")
                st.rerun()
            except Exception as exc:
                st.error(str(exc))
    with right:
        st.subheader("영수증 처리")
        receipt_infos = [
            info for info in files.list_files(receipt_dir, recursive=False) if info.path.parent == receipt_dir and not info.name.startswith(".")
        ]
        receipt_labels = [info.rel_path for info in receipt_infos]
        selected = st.multiselect("처리할 영수증", receipt_labels)
        if st.button("회의록/출장보고서 생성", disabled=not selected):
            receipt_paths = [resolve_repo_path(path) for path in selected]
            job = jobs.start_job(
                "process-receipts",
                paperwork.process_receipts_command(receipt_paths),
                metadata={"receipts": selected},
                cwd=paperwork.command_cwd(),
            )
            show_job_created(job)
    st.divider()
    tab_receipts, tab_outputs, tab_records = st.tabs(["Receipt files", "Output PDFs", "Records"])
    with tab_receipts:
        file_management_panel(files.list_files(receipt_dir), "meeting-receipts")
    with tab_outputs:
        file_management_panel(files.list_files(output_dir), "meeting-output")
    with tab_records:
        records = receipt_dir / "records.csv"
        summary = receipt_dir / "summary.csv"
        if records.exists():
            st.write("records.csv")
            st.dataframe(pd.read_csv(records), use_container_width=True)
        if summary.exists():
            st.write("summary.csv")
            st.dataframe(pd.read_csv(summary), use_container_width=True)
        if not records.exists() and not summary.exists():
            st.info("아직 ledger CSV가 없습니다.")


def jobs_page() -> None:
    st.title("Jobs")
    all_jobs = jobs.list_jobs()
    if not all_jobs:
        st.info("실행된 작업이 없습니다.")
        return
    labels = [
        f"{job.id} | {job.status.get('state', 'unknown')} | {job.status.get('kind', '')}"
        for job in all_jobs
    ]
    label = st.selectbox("작업 선택", labels)
    selected = all_jobs[labels.index(label)]
    selected = jobs.load_job(selected.id)
    status = selected.status
    cols = st.columns(4)
    cols[0].metric("상태", str(status.get("state") or "unknown"))
    cols[1].metric("종류", str(status.get("kind") or ""))
    cols[2].metric("반환 코드", "" if status.get("returncode") is None else str(status.get("returncode")))
    cols[3].metric("PID", "" if status.get("pid") is None else str(status.get("pid")))
    with st.expander("상태 JSON", expanded=False):
        st.json(status)
    with st.expander("명령", expanded=False):
        st.code(" ".join(jobs.command_for_job(selected)))
    stdout, stderr = st.tabs(["stdout", "stderr"])
    with stdout:
        st.code(jobs.read_log(selected, "stdout.log") or "(empty)")
    with stderr:
        st.code(jobs.read_log(selected, "stderr.log") or "(empty)")
    st.divider()
    st.subheader("Codex 진단")
    st.caption("safe 모드는 파일을 수정하지 않고 실패 원인과 수정 방향만 분석합니다.")
    if st.button("Codex safe 분석 시작"):
        try:
            repair_job = codex_repair.start_safe_analysis(selected)
            show_job_created(repair_job)
        except Exception as exc:
            st.error(str(exc))


def settings_page(project: projects.Project | None) -> None:
    st.title("Settings")
    st.subheader("저장소")
    st.write(f"Root: `{ROOT_DIR}`")
    st.write(f"projects.yml: `{PROJECTS_YML}`")
    st.write(f"purchase: `{PURCHASE_DIR}`")
    st.write(f"meeting: `{MEETING_DIR}`")
    st.subheader("과제")
    project_list = projects.load_projects()
    if project_list:
        st.dataframe(
            pd.DataFrame([{"key": item.key, "no": item.no, "name": item.name} for item in project_list]),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("과제 정보가 없습니다.")
    if project:
        st.write(f"선택 과제: `{project.no}`")
    st.subheader("도구")
    checks = [
        {"tool": "streamlit", "available": shutil.which("streamlit") is not None},
        {"tool": "codex", "available": shutil.which("codex") is not None},
        {"tool": "google-chrome", "available": shutil.which("google-chrome") is not None},
        {"tool": "pdftotext", "available": shutil.which("pdftotext") is not None},
    ]
    st.dataframe(pd.DataFrame(checks), use_container_width=True, hide_index=True)
    st.subheader("민감 파일")
    sensitive = [
        {"file": "secret.json", "exists": (ROOT_DIR / "secret.json").exists(), "content": "hidden"},
        {"file": "credentials.json", "exists": (ROOT_DIR / "credentials.json").exists(), "content": "hidden"},
        {"file": "scripts/documents/token.json", "exists": (ROOT_DIR / "scripts/documents/token.json").exists(), "content": "hidden"},
    ]
    st.dataframe(pd.DataFrame(sensitive), use_container_width=True, hide_index=True)


def main() -> None:
    init_page()
    project = selected_project()
    page = st.sidebar.radio("화면", ["Purchase", "Meeting", "Jobs", "Settings"], index=0)
    st.sidebar.divider()
    if st.sidebar.button("새로고침"):
        st.rerun()
    if page == "Purchase":
        purchase_page(project)
    elif page == "Meeting":
        meeting_page()
    elif page == "Jobs":
        jobs_page()
    else:
        settings_page(project)


if __name__ == "__main__":
    main()
