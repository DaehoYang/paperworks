#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import xlrd
import yaml
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


DEFAULT_PROJECT_NO = "202403110003"
DEFAULT_PROJECT_NAME = "결맞지 않은 광원을 이용한 홀로그래픽 디스플레이의 아이박스 확장"
DEFAULT_PROFILE = "/tmp/gachon-upload-profile"
DEFAULT_CASE_DIR = "purchase/260618_pmmfa"
REQUIRED_DOCUMENT_LABELS = [
    "물품검수확인서",
    "전자세금계산서",
    "견적서",
    "거래명세서",
    "통장사본",
    "사업자등록증",
]
BANK_ALIASES = {
    "하나은행": "KEB 하나은행",
    "KEB하나은행": "KEB 하나은행",
    "KEB 하나은행": "KEB 하나은행",
}


@dataclass
class Surface:
    page: Any
    frame: Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Upload purchase paperwork to Gachon portal.")
    parser.add_argument("--secret", default="secret.json")
    parser.add_argument("--projects-yml", default="projects.yml")
    parser.add_argument("--project-id", help="Project key/number in projects.yml. Defaults to --project-no.")
    parser.add_argument("--project-no", default=DEFAULT_PROJECT_NO)
    parser.add_argument("--project-name", default=DEFAULT_PROJECT_NAME)
    parser.add_argument("--profile", default=DEFAULT_PROFILE)
    parser.add_argument(
        "--case-dir",
        action="append",
        dest="case_dirs",
        help="Purchase case directory. Repeat to upload multiple line items into one claim.",
    )
    parser.add_argument("--summary", default="")
    parser.add_argument("--invoice-mail", default="sheepvs5@gmail.com")
    parser.add_argument("--add-dialog-action", choices=["accept", "dismiss"], default="accept")
    parser.add_argument("--draft-total", type=int, default=0, help="Open an existing draft claim row with this amount before appending.")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--interactive", action="store_true")
    parser.add_argument(
        "--step",
        default="prepare",
        choices=[
            "login",
            "t09",
            "project",
            "claim",
            "latest",
            "general",
            "basic",
            "invoice",
            "mail",
            "select-invoice",
            "kind",
            "payee",
            "detail",
            "inspect",
            "attach",
            "evid",
            "items",
            "save",
            "fill-save",
            "preflight",
            "prepare",
            "dump",
            "actions",
            "append-draft",
            "list-actions",
            "vendor-dump",
            "vendor-update",
            "submit-actions",
            "submit-draft",
            "fill-submit",
        ],
    )
    return parser.parse_args()


def load_projects_config(path: str | Path) -> dict[str, Any]:
    config_path = Path(path)
    if not config_path.exists():
        return {}
    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Invalid projects config: {config_path}")
    return data


def apply_project_config(options: argparse.Namespace) -> None:
    config = load_projects_config(options.projects_yml)
    if not config:
        return
    defaults = config.get("defaults") or {}
    projects = config.get("projects") or {}
    if not isinstance(projects, dict):
        raise ValueError(f'"projects" in {options.projects_yml} must be a mapping')

    project_id = options.project_id or options.project_no
    project = projects.get(str(project_id))
    if project is None:
        for candidate in projects.values():
            if isinstance(candidate, dict) and str(candidate.get("no") or "") == str(project_id):
                project = candidate
                break
    if project is None:
        if options.project_id:
            raise ValueError(f"Project {options.project_id} was not found in {options.projects_yml}")
        return
    if not isinstance(project, dict):
        raise ValueError(f"Project {project_id} in {options.projects_yml} must be a mapping")
    options.project_no = str(project.get("no") or project_id)
    options.project_name = str(project.get("name") or options.project_name)
    options.principal_investigator = str(defaults.get("principal_investigator") or "")
    options.inspector = str(defaults.get("inspector") or "")


def read_secret(secret_path: str) -> dict[str, str]:
    data = json.loads(Path(secret_path).read_text(encoding="utf-8"))
    if not data.get("id") or not data.get("pwd"):
        raise ValueError(f'{secret_path} must contain "id" and "pwd"')
    return data


def sleep(ms: int) -> None:
    time.sleep(ms / 1000)


def money_values(text: str) -> list[int]:
    values = []
    for match in re.finditer(r"(^|[^\d])(\d{1,3}(?:,\d{3})+|\d{4,})(?=[^\d]|$)", str(text)):
        values.append(int(match.group(2).replace(",", "")))
    return values


def format_money(value: int) -> str:
    return f"{value:,}"


def normalize_search_text(value: str) -> str:
    return re.sub(r"[\s()[\]{}.,/+_-]", "", str(value or "").lower())


def unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def safe_count(locator: Any) -> int:
    try:
        return locator.count()
    except PlaywrightError:
        return 0


def pdf_text(path: Path) -> str:
    try:
        return subprocess.check_output(["pdftotext", "-layout", str(path), "-"], text=True, stderr=subprocess.DEVNULL)
    except Exception:
        return ""


def compact_text(text: str) -> str:
    return re.sub(r"\s+", "", text)


def clean_account_no(value: str) -> str:
    return re.sub(r"[^\d]", "", value or "")


class PortalUploader:
    def __init__(self, options: argparse.Namespace):
        self.options = options
        self.secret: dict[str, str] | None = None
        self.playwright = None
        self.context = None
        self.page = None
        self.case_dir = Path((options.case_dirs or [DEFAULT_CASE_DIR])[0])
        self.purchase_items_info: dict[str, Any] | None = None
        self.document_info: dict[str, Any] | None = None
        self.selected_invoice_row_text = ""
        self.invoice_supplier_info: dict[str, Any] | None = None
        self.dialog_messages: list[str] = []
        self.add_dialog_action = options.add_dialog_action

    def start(self) -> None:
        self.playwright = sync_playwright().start()
        self.context = self.playwright.chromium.launch_persistent_context(
            self.options.profile,
            headless=not self.options.headed,
            executable_path="/usr/bin/google-chrome",
            args=["--no-sandbox"],
            viewport={"width": 1440, "height": 950},
            locale="ko-KR",
            accept_downloads=True,
        )
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()

        def accept_dialog(dialog: Any) -> None:
            print(f"dialog: {dialog.message}")
            self.dialog_messages.append(str(dialog.message))
            try:
                if "내역을 추가하시겠습니까" in str(dialog.message) and self.add_dialog_action == "dismiss":
                    dialog.dismiss()
                else:
                    dialog.accept()
            except PlaywrightError:
                pass

        self.page.on("dialog", accept_dialog)
        self.context.on("page", lambda page: page.on("dialog", accept_dialog))

    def close(self) -> None:
        if self.context:
            self.context.close()
        if self.playwright:
            self.playwright.stop()

    def wait_for_dialogs_to_settle(self, timeout: int = 30000, quiet: int = 3000, min_wait: int = 5000) -> None:
        started = time.monotonic()
        last_count = len(self.dialog_messages)
        last_change = started
        while (time.monotonic() - started) * 1000 < timeout:
            try:
                page = self.page or (self.context.pages[0] if self.context.pages else None)
                if page:
                    page.wait_for_timeout(1000)
                else:
                    sleep(1000)
            except PlaywrightError:
                sleep(1000)
            current_count = len(self.dialog_messages)
            if current_count != last_count:
                last_count = current_count
                last_change = time.monotonic()
            if (time.monotonic() - started) * 1000 >= min_wait and (time.monotonic() - last_change) * 1000 >= quiet:
                return

    def text_of(self, frame: Any, timeout: int = 1200) -> str:
        try:
            text = frame.locator("body").inner_text(timeout=timeout)
        except PlaywrightError:
            text = ""
        return re.sub(r"\s+", " ", text).strip()

    def wait_frame_by_text(self, pattern: re.Pattern[str], timeout: int = 30000) -> Any | None:
        started = time.monotonic()
        while (time.monotonic() - started) * 1000 < timeout:
            for frame in self.page.frames:
                if pattern.search(self.text_of(frame, 800)):
                    return frame
            sleep(800)
        return None

    def wait_frame_by_selector(self, selector: str, timeout: int = 30000) -> Any | None:
        started = time.monotonic()
        while (time.monotonic() - started) * 1000 < timeout:
            for frame in self.page.frames:
                if safe_count(frame.locator(selector)):
                    return frame
            sleep(800)
        return None

    def first_visible(self, frame: Any, selector: str) -> Any:
        visible = frame.locator(f"{selector}:visible")
        if safe_count(visible):
            return visible.first
        locator = frame.locator(selector)
        for idx in range(safe_count(locator)):
            item = locator.nth(idx)
            try:
                if item.is_visible():
                    return item
            except PlaywrightError:
                pass
        return locator.first

    def click_first_visible(self, frame: Any, selector: str, timeout: int = 10000) -> None:
        locator = self.first_visible(frame, selector)
        try:
            locator.click(force=True, timeout=timeout)
        except PlaywrightError as error:
            clicked = False
            try:
                clicked = locator.evaluate(
                    """element => {
                        if (typeof element.click === "function") {
                            element.click();
                            return true;
                        }
                        return false;
                    }"""
                )
            except PlaywrightError:
                pass
            if not clicked:
                raise error

    def set_case_dir(self, case_dir: str | Path) -> None:
        self.case_dir = Path(case_dir)
        self.purchase_items_info = None
        self.document_info = None
        self.selected_invoice_row_text = ""
        self.invoice_supplier_info = None

    def supplier_registration_info(self) -> dict[str, str]:
        documents = self.classify_case_documents()["labels"]
        texts: dict[str, str] = {}
        for label, paths in documents.items():
            texts[label] = "\n".join(pdf_text(path) for path in paths)
        all_text = "\n".join(texts.values())

        business_no = ""
        supplier = self.get_invoice_supplier_info()
        if supplier.get("business_no"):
            business_no = str(supplier["business_no"])
        if not business_no:
            numbers = re.findall(r"\b\d{3}-\d{2}-\d{5}\b", all_text)
            business_no = next((number for number in numbers if number != "129-82-07687"), numbers[0] if numbers else "")

        business_text = texts.get("사업자등록증", "")
        statement_text = texts.get("거래명세서", "")
        invoice_text = texts.get("전자세금계산서", "")

        name = str(supplier.get("name") or "")
        if not name:
            for pattern in [
                r"법\s*인\s*명\s*\([^)]*\)\s*:\s*([^\n]+)",
                r"상\s*호\s+(?:\([^)]*\)\s*)?([^\s]+)",
            ]:
                match = re.search(pattern, business_text + "\n" + statement_text)
                if match:
                    name = match.group(1).strip()
                    break

        representative = ""
        for pattern in [
            r"대\s*표\s*자\s*:\s*([^\n]+)",
            r"성\s*명\s+([가-힣A-Za-z]+)",
        ]:
            match = re.search(pattern, business_text + "\n" + statement_text)
            if match:
                representative = re.sub(r"\s*\(.*$", "", match.group(1)).strip()
                break

        address = ""
        for pattern in [
            r"사\s*업\s*장\s*소\s*재\s*지\s*:\s*([^\n]+)",
            r"공\s*급\s*자.*?사업장\s*([^\n]+)",
            r"주\s*소\s+([^\n]+)",
        ]:
            match = re.search(pattern, business_text + "\n" + invoice_text + "\n" + statement_text, re.S)
            if match:
                address = re.sub(r"\s+", " ", match.group(1)).strip()
                address = re.sub(r"\s*(본\s*점|사\s*업|업\s*태|이메일).*$", "", address).strip()
                break

        bank = ""
        account_no = ""
        account_holder = ""
        match = re.search(r"입금계좌\s*정보\s*:\s*([^()\n]+)\(([^)]+)\)\s*,?\s*예금주\s*:\s*([^\n]+)", statement_text)
        if match:
            bank = match.group(1).strip()
            account_no = clean_account_no(match.group(2))
            account_holder = match.group(3).strip()
        if bank:
            bank = BANK_ALIASES.get(bank, bank)

        phone = ""
        match = re.search(r"(?:전화번호|Tel\s*:?)\s*([0-9+\-() ]{8,})", statement_text, re.I)
        if match:
            phone = re.sub(r"\s+", "", match.group(1)).strip()
            phone = re.sub(r"^82-2-", "02-", phone)

        postal_code = ""
        match = re.search(r"\((\d{5})\)", all_text)
        if match:
            postal_code = match.group(1)

        email = ""
        for source in [business_text, statement_text, invoice_text, all_text]:
            match = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", source)
            if match:
                email = match.group(0)
                break

        info = {
            "name": name,
            "business_no": business_no,
            "business_no_digits": business_no.replace("-", ""),
            "representative": representative,
            "address": address,
            "postal_code": postal_code,
            "bank": bank,
            "account_no": account_no,
            "account_holder": account_holder,
            "phone": phone,
            "email": email,
        }
        print("supplier registration info:")
        for key, value in info.items():
            if value:
                print(f"  {key}: {value}")
        return info

    def find_button_by_text_or_id(self, frame: Any, pattern: re.Pattern[str]) -> Any:
        candidates = frame.locator("a:visible, button:visible, input[type='button']:visible")
        for idx in range(safe_count(candidates)):
            locator = candidates.nth(idx)
            try:
                meta = locator.evaluate(
                    """element => ({
                        id: element.id,
                        name: element.name,
                        value: element.value,
                        text: element.innerText,
                        title: element.title
                    })"""
                )
            except PlaywrightError:
                continue
            if pattern.search(json.dumps(meta, ensure_ascii=False)):
                return locator
        raise RuntimeError(f"Button matching {pattern.pattern} was not found")

    def select_by_text(self, frame: Any, selector: str, pattern: str) -> None:
        locator = self.first_visible(frame, selector)
        options = locator.evaluate(
            """(element, source) => {
                const re = new RegExp(source);
                return Array.from(element.options)
                    .map(option => ({value: option.value, text: option.textContent.trim()}))
                    .filter(option => re.test(option.text));
            }""",
            pattern,
        )
        if not options:
            raise RuntimeError(f"No option matching {pattern} for {selector}")
        locator.select_option(options[0]["value"])
        locator.evaluate("""element => element.dispatchEvent(new Event("change", {bubbles: true}))""")
        sleep(4500)
        print(f"selected {selector}: {options[0]['text']} ({options[0]['value']})")

    def close_known_popups(self) -> None:
        for _ in range(4):
            locator = self.page.locator(
                ", ".join(
                    [
                        "button.btn_close.close:visible",
                        "button:has-text('닫기'):visible",
                        "#btn-confirm:visible",
                        "button:has-text('다음(확인)'):visible",
                    ]
                )
            ).first
            try:
                locator.click(force=True, timeout=2500)
                sleep(800)
            except PlaywrightError:
                break

    def ensure_portal_login(self) -> None:
        if self.secret is None:
            self.secret = read_secret(self.options.secret)
        self.page.goto("https://portal.gachon.ac.kr/", wait_until="domcontentloaded", timeout=45000)
        sleep(2500)
        try:
            self.page.wait_for_selector("#user_id", timeout=10000)
        except PlaywrightTimeoutError:
            pass
        if safe_count(self.page.locator("#user_id")):
            self.page.fill("#user_id", self.secret["id"])
            self.page.fill("#user_password", self.secret["pwd"])
            login_button = self.page.locator("button:has-text('로그인'):visible").first
            if not safe_count(login_button):
                login_button = self.page.locator("a:has-text('로그인'):visible").first
            if not safe_count(login_button):
                login_button = self.page.locator("button[type='submit']:visible").first
            login_button.click(force=True, timeout=10000)
            sleep(6000)
            postpone = self.page.locator("a.btnNo")
            if safe_count(postpone):
                try:
                    postpone.first.evaluate("element => element.click()")
                except PlaywrightError:
                    postpone.first.click(force=True)
                sleep(6000)
            next_time = self.page.locator("a:has-text('다음에 변경'):visible, button:has-text('다음에 변경'):visible").first
            if safe_count(next_time):
                try:
                    next_time.click(force=True, timeout=5000)
                    sleep(3000)
                except PlaywrightError:
                    pass
        print(f"portal: {self.page.title()} {self.page.url}")

    def go_to_research_portal(self) -> Any:
        for attempt in range(1, 4):
            self.ensure_portal_login()
            self.page.goto("https://portal.gachon.ac.kr/p/T09/", wait_until="domcontentloaded", timeout=45000)
            sleep(9000)
            self.close_known_popups()
            frame = self.wait_frame_by_text(re.compile(re.escape(self.options.project_no)), 7000)
            if frame:
                print(f"T09 ready: {frame.url}")
                return frame
            print(f"T09 retry {attempt}")
        raise RuntimeError("T09 project dashboard was not found")

    def open_project(self) -> None:
        dashboard = self.wait_frame_by_text(re.compile(re.escape(self.options.project_no)), 10000) or self.go_to_research_portal()
        self.close_known_popups()
        direct = dashboard.locator(f"a[onclick*=\"fnPage('01','{self.options.project_no}'\"]").first
        if safe_count(direct):
            direct.evaluate("element => element.click()")
        else:
            row = dashboard.locator(f"xpath=//*[normalize-space(text())='{self.options.project_no}']/ancestor::tr[1]")
            row.first.locator("a").filter(has_text=self.options.project_name).first.click(force=True, timeout=10000)
        sleep(5000)
        print(f"project opened: {self.options.project_no} {self.options.project_name}")

    def open_claim_tab(self) -> None:
        frame = self.wait_frame_by_text(re.compile("기본정보.*청구서"), 10000)
        if not frame:
            self.open_project()
            frame = self.wait_frame_by_text(re.compile("기본정보.*청구서"), 15000)
        if not frame:
            raise RuntimeError("Project detail frame was not found")
        self.close_known_popups()
        frame.locator("a").filter(has_text="청구서").first.click(force=True, timeout=10000)
        sleep(5000)
        print("claim tab opened")

    def open_general_claim(self) -> None:
        frame = self.wait_frame_by_selector("#P10", 10000)
        if not frame:
            self.open_claim_tab()
            frame = self.wait_frame_by_selector("#P10", 15000)
        if not frame:
            raise RuntimeError("Claim list frame was not found")
        frame.locator("#P10").first.click(force=True, timeout=10000)
        sleep(7000)
        print("general claim opened")

    def claim_list_frame(self) -> Any:
        frame = self.wait_frame_by_selector("#P10", 10000)
        if not frame:
            self.open_claim_tab()
            frame = self.wait_frame_by_selector("#P10", 15000)
        if not frame:
            raise RuntimeError("Claim list frame was not found")
        return frame

    def open_latest_draft_claim(self, expected_total: int = 0) -> None:
        frame = self.claim_list_frame()
        total_text = format_money(expected_total) if expected_total else ""
        selected = frame.evaluate(
            """totalText => {
                const normalize = value => String(value || "").replace(/\\s+/g, " ").trim();
                if (window.jQuery && window.jQuery.fn && window.jQuery.fn.jqGrid) {
                    const grids = Array.from(document.querySelectorAll("table.ui-jqgrid-btable, table[id]"));
                    for (const grid of grids) {
                        const jq = window.jQuery(grid);
                        let ids = [];
                        try {
                            ids = jq.jqGrid("getDataIDs") || [];
                        } catch (error) {
                            ids = [];
                        }
                        for (const id of ids) {
                            let data = {};
                            try {
                                data = jq.jqGrid("getRowData", id) || {};
                            } catch (error) {
                                data = {};
                            }
                            const row = document.getElementById(id);
                            const text = normalize(`${Object.values(data).join(" ")} ${row ? row.innerText || row.textContent || "" : ""}`);
                            if (!/임시저장/.test(text)) continue;
                            if (totalText && !text.includes(totalText)) continue;
                            jq.jqGrid("setSelection", id, true);
                            if (row) {
                                row.scrollIntoView({block: "center", inline: "center"});
                                row.dispatchEvent(new MouseEvent("mousedown", {bubbles: true, cancelable: true, view: window}));
                                row.dispatchEvent(new MouseEvent("mouseup", {bubbles: true, cancelable: true, view: window}));
                                row.dispatchEvent(new MouseEvent("click", {bubbles: true, cancelable: true, view: window}));
                            }
                            return {ok: true, text};
                        }
                    }
                }
                let rows = Array.from(document.querySelectorAll(".jgrid-row, tr.jqgrow, tr[role='row'], tbody tr"))
                    .filter(row => {
                        const text = normalize(row.innerText || row.textContent || "");
                        if (!text || /No\\s*결의서구분|결의번호/.test(text)) return false;
                        if (!/임시저장/.test(text)) return false;
                        if (totalText && !text.includes(totalText)) return false;
                        return true;
                    });
                if (!rows.length) {
                    rows = Array.from(document.querySelectorAll("td, span, div, a"))
                        .filter(element => {
                            const text = normalize(element.innerText || element.textContent || "");
                            return /임시저장/.test(text) && (!totalText || text.includes(totalText));
                        })
                        .map(element => element.closest(".jgrid-row, tr.jqgrow, tr[role='row'], tr") || element.closest("div"))
                        .filter(Boolean);
                }
                const row = rows[0];
                if (!row) return {ok: false, text: ""};
                row.scrollIntoView({block: "center", inline: "center"});
                const checkbox = row.querySelector("input[type='checkbox'].checkmg, input[type='checkbox']");
                if (checkbox && !checkbox.checked) {
                    checkbox.click();
                    checkbox.checked = true;
                    checkbox.dispatchEvent(new Event("change", {bubbles: true}));
                }
                const table = row.closest("table");
                if (table && row.id && window.jQuery && window.jQuery.fn && window.jQuery.fn.jqGrid) {
                    window.jQuery(table).jqGrid("setSelection", row.id, true);
                }
                for (const element of [row, row.querySelector("td")].filter(Boolean)) {
                    element.dispatchEvent(new MouseEvent("mousedown", {bubbles: true, cancelable: true, view: window}));
                    element.dispatchEvent(new MouseEvent("mouseup", {bubbles: true, cancelable: true, view: window}));
                    element.dispatchEvent(new MouseEvent("click", {bubbles: true, cancelable: true, view: window}));
                    if (typeof element.click === "function") element.click();
                }
                return {ok: true, text: normalize(row.innerText || row.textContent || "")};
            }""",
            total_text,
        )
        if not selected.get("ok"):
            self.dump("latest-draft-not-found")
            suffix = f" with total {total_text}" if total_text else ""
            raise RuntimeError(f"Draft claim row{suffix} was not found")
        detail = self.find_button_by_text_or_id(frame, re.compile("상세조회/변경|상세조회|btn.*detail|btn.*mod|modify", re.I))
        detail.click(force=True, timeout=10000)
        sleep(7000)
        if not self.wait_frame_by_selector("#PAY_EXP_CD", 15000):
            self.dump("latest-draft-open-failed")
            raise RuntimeError("Draft claim detail did not open")
        print(f"draft claim opened: {selected.get('text')[:180]}")

    def reset_detail_form_for_add(self) -> None:
        frame = self.general_claim_frame()
        add_button = frame.locator("#btn_listAdd:visible").first
        if safe_count(add_button):
            return
        init_button = frame.locator("#btn_listInit:visible").first
        if safe_count(init_button):
            init_button.click(force=True, timeout=10000)
            sleep(1500)
        if not safe_count(frame.locator("#btn_listAdd:visible")):
            print("warning: detail add button is not visible after reset")

    def general_claim_frame(self) -> Any:
        frame = self.wait_frame_by_selector("#PAY_EXP_CD", 10000)
        if not frame:
            self.open_general_claim()
            frame = self.wait_frame_by_selector("#PAY_EXP_CD", 15000)
        if not frame:
            raise RuntimeError("General claim form was not found")
        return frame

    def select_budget_and_receipt(self) -> None:
        frame = self.general_claim_frame()
        self.select_by_text(frame, "#PAY_EXP_CD", "연구재료비")
        self.select_by_text(frame, "#DTL_EXP_CD", "연구재료.*구입비")
        self.select_by_text(frame, "#EVDI_DIV_CD", "세금계산서")
        print("budget and receipt selected")

    def open_invoice_selector(self) -> None:
        frame = self.general_claim_frame()
        self.click_first_visible(frame, "#onlyEvdiDivList")
        sleep(5000)
        print(f"invoice selector clicked; pages={len(self.context.pages)}")

    def invoice_search_surface(self) -> Surface | None:
        pattern = re.compile("매입.*계산서|계산서수신메일|수신메일|세금계산서.*조회")
        started = time.monotonic()
        while (time.monotonic() - started) * 1000 < 30000:
            for page in self.context.pages:
                for frame in page.frames:
                    if safe_count(frame.locator("#TAX_RCV_EMAIL_V, #btn_inquiry")):
                        return Surface(page, frame)
            for page in self.context.pages:
                for frame in page.frames:
                    if pattern.search(self.text_of(frame, 800)):
                        return Surface(page, frame)
            sleep(800)
        return None

    def click_direct_mail_checkbox(self, frame: Any) -> None:
        mail_input = frame.locator("#TAX_RCV_EMAIL_V").first
        self_enter = frame.locator("#self_enter").first
        if safe_count(self_enter):
            try:
                if not self_enter.is_checked():
                    self_enter.check(force=True, timeout=5000)
            except PlaywrightError:
                pass
            sleep(800)
            if safe_count(mail_input):
                try:
                    if not mail_input.evaluate("element => element.readOnly"):
                        return
                except PlaywrightError:
                    pass
            self_enter.evaluate(
                """element => {
                    element.checked = true;
                    element.dispatchEvent(new Event("change", {bubbles: true}));
                }"""
            )
            if safe_count(mail_input):
                mail_input.evaluate(
                    """element => {
                        element.readOnly = false;
                        element.classList.remove("dis");
                    }"""
                )
            sleep(300)
            return

        if safe_count(mail_input):
            clicked = False
            try:
                clicked = mail_input.evaluate(
                    """element => {
                        const container = element.closest("tr, li, div, table") || document;
                        const candidates = Array.from(container.querySelectorAll("input[type='checkbox'], input[type='radio']"));
                        const direct = candidates.find(candidate => {
                            const label = candidate.closest("label") ||
                                document.querySelector(`label[for="${candidate.id}"]`) ||
                                candidate.parentElement;
                            return label && /직접입력/.test(label.innerText || label.textContent || "");
                        });
                        const target = direct || candidates[candidates.length - 1];
                        if (!target) return false;
                        target.click();
                        target.checked = true;
                        target.dispatchEvent(new Event("click", {bubbles: true}));
                        target.dispatchEvent(new Event("change", {bubbles: true}));
                        return true;
                    }"""
                )
            except PlaywrightError:
                pass
            if clicked:
                sleep(800)
                try:
                    if not mail_input.evaluate("element => element.readOnly"):
                        return
                except PlaywrightError:
                    pass
        labels = frame.locator("label").filter(has_text=re.compile("직접입력"))
        if safe_count(labels):
            labels.first.click(force=True, timeout=5000)
            sleep(800)

    def find_mail_search_input(self, frame: Any) -> Any:
        candidates = frame.locator("input[type='text']:visible")
        for idx in range(safe_count(candidates)):
            locator = candidates.nth(idx)
            try:
                meta = locator.evaluate(
                    """element => ({
                        id: element.id,
                        name: element.name,
                        placeholder: element.placeholder,
                        value: element.value
                    })"""
                )
            except PlaywrightError:
                continue
            if re.search(r"mail|email|메일|수신|S_", json.dumps(meta, ensure_ascii=False), re.I):
                return locator
        if safe_count(candidates):
            return candidates.first
        raise RuntimeError("Mail search input was not found")

    def search_invoice_by_mail(self, mail_keyword: str) -> None:
        surface = self.invoice_search_surface()
        if not surface:
            raise RuntimeError("Invoice search surface was not found")
        frame = surface.frame
        self.click_direct_mail_checkbox(frame)
        input_locator = frame.locator("#TAX_RCV_EMAIL_V").first if safe_count(frame.locator("#TAX_RCV_EMAIL_V")) else self.find_mail_search_input(frame)
        try:
            if input_locator.evaluate("element => element.readOnly"):
                input_locator.evaluate(
                    """element => {
                        element.readOnly = false;
                        element.classList.remove("dis");
                        element.dispatchEvent(new Event("change", {bubbles: true}));
                    }"""
                )
        except PlaywrightError:
            pass
        input_locator.fill(mail_keyword)
        search_button = frame.locator("#btn_inquiry").first if safe_count(frame.locator("#btn_inquiry")) else self.find_button_by_text_or_id(frame, re.compile("검색|조회|inquiry|btn.*search|Search", re.I))
        search_button.click(force=True, timeout=10000)
        sleep(5000)
        print(f"invoice searched by mail keyword: {mail_keyword}")

    def case_path(self, *parts: str) -> Path:
        return self.case_dir.joinpath(*parts).resolve()

    def classify_case_documents(self) -> dict[str, Any]:
        if self.document_info:
            return self.document_info
        case_dir = self.case_path()
        if not case_dir.is_dir():
            raise RuntimeError(f"Case directory was not found: {case_dir}")

        labels: dict[str, list[Path]] = {label: [] for label in REQUIRED_DOCUMENT_LABELS}

        inspection = case_dir / "물품검수확인서_작성.pdf"
        if inspection.exists():
            labels["물품검수확인서"].append(inspection)

        pdf_paths = sorted(
            [path for path in case_dir.iterdir() if path.is_file() and path.suffix.lower() == ".pdf"],
            key=lambda path: [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", path.stem)],
        )
        for path in pdf_paths:
            text = pdf_text(path)
            compact = compact_text(text).upper()
            name = path.name

            if re.search(r"물품검수확인서", name) or "거래명세서순서대로사진" in compact:
                if path not in labels["물품검수확인서"]:
                    labels["물품검수확인서"].append(path)
                continue
            if re.search(r"전세|세금계산서", name) or ("전자세금계산서" in compact and "승인번호" in compact):
                labels["전자세금계산서"].append(path)
            if "견적서" in compact or "QUOTATION" in compact or "견적합니다" in compact or "견적드리" in compact:
                labels["견적서"].append(path)
            if "거래명세서" in compact or "거래명세" in compact or ("납품일자" in compact and "공급가액" in compact):
                labels["거래명세서"].append(path)
            if re.search(r"통장|계좌", name) or "통장사본" in compact or "입금계좌" in compact or "계좌번호" in compact:
                labels["통장사본"].append(path)
            if re.search(r"사업자|등록증", name) or "사업자등록증" in compact or ("사업자등록번호" in compact and "개업연월일" in compact):
                labels["사업자등록증"].append(path)

        missing = [label for label, paths in labels.items() if not paths]
        if missing:
            detail = ", ".join(f"{label}: {[str(path.name) for path in paths]}" for label, paths in labels.items())
            raise RuntimeError(f"Missing required documents in {case_dir}: {', '.join(missing)}. detected={detail}")

        upload_paths: list[Path] = []
        for label in REQUIRED_DOCUMENT_LABELS:
            for path in labels[label]:
                if path not in upload_paths:
                    upload_paths.append(path)

        self.document_info = {"labels": labels, "upload_paths": upload_paths}
        print(f"document check passed: {case_dir}")
        for label in REQUIRED_DOCUMENT_LABELS:
            print(f"  {label}: {', '.join(path.name for path in labels[label])}")
        return self.document_info

    def get_purchase_items_info(self) -> dict[str, Any]:
        if self.purchase_items_info:
            return self.purchase_items_info
        xls_path = self.case_path("items.xls")
        if not xls_path.exists():
            raise RuntimeError(f"Missing purchase item excel: {xls_path}")
        book = xlrd.open_workbook(str(xls_path))
        sheet = book.sheet_by_index(0)
        headers = [str(sheet.cell_value(0, col)).strip() for col in range(sheet.ncols)]

        def col(*names: str) -> int:
            for name in names:
                if name in headers:
                    return headers.index(name)
            raise RuntimeError(f"missing column: {'/'.join(names)}")

        def value(row: int, idx: int) -> Any:
            raw = sheet.cell_value(row, idx)
            if isinstance(raw, float) and raw.is_integer():
                return int(raw)
            return raw

        def number(row: int, idx: int) -> int:
            raw = value(row, idx)
            if raw in ("", None):
                return 0
            return int(round(float(str(raw).replace(",", ""))))

        name_idx = col("품명")
        qty_idx = col("수량")
        total_idx = col("총구입액")
        items = []
        for row in range(1, sheet.nrows):
            name = str(value(row, name_idx)).strip()
            if name:
                items.append({"name": name, "quantity": number(row, qty_idx), "total": number(row, total_idx)})
        total = sum(item["total"] for item in items)
        if not items:
            raise RuntimeError(f"No purchase items found in {xls_path}")
        if not total:
            raise RuntimeError(f"Purchase item total is zero in {xls_path}")
        self.purchase_items_info = {"items": items, "total": total}
        return self.purchase_items_info

    def summary_text(self) -> str:
        if self.options.summary:
            return self.options.summary
        items = self.get_purchase_items_info()["items"]
        first_name = items[0]["name"]
        return first_name if len(items) == 1 else f"{first_name} 외 {len(items) - 1}"

    def get_invoice_supplier_info(self) -> dict[str, Any]:
        if self.invoice_supplier_info:
            return self.invoice_supplier_info
        invoice_path = self.classify_case_documents()["labels"]["전자세금계산서"][0]
        if not invoice_path.exists():
            self.invoice_supplier_info = {"name": "", "business_no": "", "tokens": []}
            return self.invoice_supplier_info
        try:
            text = pdf_text(invoice_path)
        except Exception as error:
            print(f"warning: could not read invoice PDF supplier info: {error}")
            text = ""
        compact_all = compact_text(text)
        business_numbers = re.findall(r"\b\d{3}-\d{2}-\d{5}\b", compact_all) or re.findall(r"\b\d{3}-\d{2}-\d{5}\b", text)
        business_no = next((number for number in business_numbers if number != "129-82-07687"), business_numbers[0] if business_numbers else "")
        name = ""
        for line in text.splitlines():
            compact = re.sub(r"\s+", "", line)
            match = re.search(r"(주식회사[가-힣A-Za-z0-9]+|\(주\)[가-힣A-Za-z0-9]+|[가-힣A-Za-z0-9]+주식회사)", compact)
            if match:
                name = re.sub(r"(성명|공급|받는|사업장|업태|종목|\(법인명\)).*$", "", match.group(1))
                name = re.sub(r"[공급받는자]+$", "", name)
                break
        if not name:
            lines = text.splitlines()
            for idx, line in enumerate(lines):
                if "상호" not in line:
                    continue
                for next_line in lines[idx + 1 : idx + 4]:
                    compact = re.sub(r"\s+", "", next_line)
                    match = re.match(r"([가-힣A-Za-z0-9().㈜]+?)성", compact)
                    if match and "가천대학교" not in match.group(1):
                        name = match.group(1)
                        break
                if name:
                    break
        bare_name = re.sub(r"^주식회사|^\(주\)|주식회사$", "", name)
        tokens = [
            token
            for token in unique([name, bare_name, business_no, business_no.replace("-", "")])
            if len(normalize_search_text(token)) >= 2
        ]
        self.invoice_supplier_info = {"name": name, "business_no": business_no, "tokens": tokens}
        return self.invoice_supplier_info

    def attachment_files(self) -> list[dict[str, Any]]:
        info = self.classify_case_documents()
        return [{"label": "첨부문서", "path": path} for path in info["upload_paths"]]

    def validate_attachment_files(self) -> None:
        self.classify_case_documents()
        for file in self.attachment_files():
            if not file["path"].exists():
                raise RuntimeError(f"Missing {file['label']}: {file['path']}")

    def select_invoice_row_by_total(self, frame: Any, expected_total: int) -> str:
        rows = frame.locator("tr.jqgrow:visible, tr[role='row']:visible:has(td), tbody tr:visible:has(td)")
        selected_row = None
        selected_row_text = ""
        for idx in range(safe_count(rows)):
            row = rows.nth(idx)
            try:
                row_text = re.sub(r"\s+", " ", row.inner_text()).strip()
            except PlaywrightError:
                continue
            if not row_text or re.search("승인번호|작성일자|공급가액|합계", row_text):
                continue
            if expected_total in money_values(row_text):
                selected_row = row
                selected_row_text = row_text
                break

        selected_by_dom = False
        if not selected_row:
            target_text = format_money(expected_total)
            selected = frame.evaluate(
                """targetText => {
                    const normalize = value => String(value || "").replace(/\\s+/g, " ").trim();
                    const elements = Array.from(document.querySelectorAll("td, span, div, a"))
                        .filter(element => normalize(element.innerText || element.textContent).includes(targetText))
                        .sort((a, b) => normalize(a.innerText || a.textContent).length - normalize(b.innerText || b.textContent).length);
                    const amountCell = elements[0];
                    if (!amountCell) return {ok: false, text: ""};
                    const row = amountCell.closest("tr.jqgrow, tr[role='row'], tr") || amountCell;
                    const rowText = normalize(row.innerText || row.textContent || amountCell.innerText || amountCell.textContent);
                    row.scrollIntoView({block: "center", inline: "center"});
                    const radio = row.querySelector("input[type='radio'][name='radioJGM4'], input[type='radio']");
                    if (radio) {
                        radio.checked = true;
                        radio.dispatchEvent(new MouseEvent("click", {bubbles: true, cancelable: true, view: window}));
                        radio.dispatchEvent(new Event("change", {bubbles: true}));
                    }
                    const grid = row.closest("table");
                    if (grid && row.id && window.jQuery && window.jQuery.fn && window.jQuery.fn.jqGrid) {
                        window.jQuery(grid).jqGrid("setSelection", row.id, true);
                    }
                    for (const element of [amountCell, row]) {
                        element.dispatchEvent(new MouseEvent("mousedown", {bubbles: true, cancelable: true, view: window}));
                        element.dispatchEvent(new MouseEvent("mouseup", {bubbles: true, cancelable: true, view: window}));
                        element.dispatchEvent(new MouseEvent("click", {bubbles: true, cancelable: true, view: window}));
                        if (typeof element.click === "function") element.click();
                    }
                    return {ok: true, text: rowText};
                }""",
                target_text,
            )
            selected_by_dom = bool(selected.get("ok"))
            selected_row_text = selected.get("text") or selected_row_text
        if not selected_row:
            if not selected_by_dom:
                return ""
            sleep(800)
        else:
            radio = selected_row.locator("input[type='radio'][name='radioJGM4'], input[type='radio']").first
            if safe_count(radio):
                radio.check(force=True, timeout=10000)
                sleep(800)
            else:
                try:
                    selected_row.click(force=True, timeout=10000)
                except PlaywrightError:
                    selected_row.evaluate(
                        """row => {
                            row.scrollIntoView({block: "center", inline: "center"});
                            row.dispatchEvent(new MouseEvent("mousedown", {bubbles: true, cancelable: true}));
                            row.dispatchEvent(new MouseEvent("mouseup", {bubbles: true, cancelable: true}));
                            row.dispatchEvent(new MouseEvent("click", {bubbles: true, cancelable: true}));
                            if (typeof row.click === "function") row.click();
                        }"""
                    )
                sleep(800)
        return selected_row_text

    def search_invoice_without_mail_filter(self) -> None:
        surface = self.invoice_search_surface()
        if not surface:
            raise RuntimeError("Invoice search surface was not found")
        frame = surface.frame
        mail_input = frame.locator("#TAX_RCV_EMAIL_V").first
        if safe_count(mail_input):
            try:
                mail_input.evaluate(
                    """element => {
                        element.readOnly = false;
                        element.classList.remove("dis");
                    }"""
                )
                mail_input.fill("")
            except PlaywrightError:
                pass
        search_button = frame.locator("#btn_inquiry").first if safe_count(frame.locator("#btn_inquiry")) else self.find_button_by_text_or_id(frame, re.compile("검색|조회|inquiry|btn.*search|Search", re.I))
        search_button.click(force=True, timeout=10000)
        sleep(5000)
        print("invoice searched without mail filter")

    def search_invoice_by_supplier_business_no(self) -> None:
        supplier = self.get_invoice_supplier_info()
        business_no = supplier.get("business_no") or ""
        if not business_no:
            return
        surface = self.invoice_search_surface()
        if not surface:
            raise RuntimeError("Invoice search surface was not found")
        frame = surface.frame
        mail_input = frame.locator("#TAX_RCV_EMAIL_V").first
        if safe_count(mail_input):
            try:
                mail_input.evaluate(
                    """element => {
                        element.readOnly = false;
                        element.classList.remove("dis");
                    }"""
                )
                mail_input.fill("")
            except PlaywrightError:
                pass
        corp_select = frame.locator("#SELR_CORP").first
        if safe_count(corp_select):
            try:
                corp_select.select_option("공급자(거래처)사업자등록번호")
            except PlaywrightError:
                pass
        supplier_input = frame.locator("#CORP_TXT").first if safe_count(frame.locator("#CORP_TXT")) else self.find_input_near_label(
            frame,
            re.compile("공급자|거래처|사업자등록번호"),
            ["#BIZ_NO", "#S_BIZ_NO", "#SUPP_BIZ_NO", "#S_SUPP_BIZ_NO", "#CUST_BIZ_NO"],
        )
        supplier_input.fill(business_no)
        search_button = frame.locator("#btn_inquiry").first if safe_count(frame.locator("#btn_inquiry")) else self.find_button_by_text_or_id(frame, re.compile("검색|조회|inquiry|btn.*search|Search", re.I))
        search_button.click(force=True, timeout=10000)
        sleep(5000)
        print(f"invoice searched by supplier business no: {business_no}")

    def select_first_invoice_result(self) -> None:
        surface = self.invoice_search_surface()
        if not surface:
            raise RuntimeError("Invoice search surface was not found")
        frame = surface.frame
        expected_total = self.get_purchase_items_info()["total"]
        text = self.text_of(frame, 1200)
        if re.search(r"현재\s*0\s*건|총\s*0\s*건", text):
            self.dump("invoice-no-result")
            raise RuntimeError("Invoice search returned no rows")

        selected_row_text = self.select_invoice_row_by_total(frame, expected_total)
        if not selected_row_text:
            self.search_invoice_by_supplier_business_no()
            surface = self.invoice_search_surface()
            if not surface:
                raise RuntimeError("Invoice search surface was not found after supplier search")
            frame = surface.frame
            selected_row_text = self.select_invoice_row_by_total(frame, expected_total)
        if not selected_row_text:
            self.search_invoice_without_mail_filter()
            surface = self.invoice_search_surface()
            if not surface:
                raise RuntimeError("Invoice search surface was not found after fallback search")
            frame = surface.frame
            selected_row_text = self.select_invoice_row_by_total(frame, expected_total)
        if not selected_row_text:
            self.dump("invoice-total-mismatch")
            raise RuntimeError(f"No invoice row matched items.xls total {format_money(expected_total)}원")

        ok = frame.locator("#btn_out").first if safe_count(frame.locator("#btn_out")) else self.find_button_by_text_or_id(frame, re.compile("확인|선택|적용|btn.*out|btn.*ok", re.I))
        ok.click(force=True, timeout=10000)
        sleep(4000)
        self.selected_invoice_row_text = selected_row_text
        print(f"invoice result selected: matched total {format_money(expected_total)}원 ({selected_row_text[:140]})")

    def set_claim_kind_consumable(self) -> None:
        frame = self.general_claim_frame()
        frame.locator("#LB_85, label:has-text('소모성물품')").first.click(force=True, timeout=10000)
        sleep(1000)
        print("claim kind selected: 소모성물품")

    def payee_search_surface(self, timeout: int = 20000) -> Surface | None:
        started = time.monotonic()
        while (time.monotonic() - started) * 1000 < timeout:
            for page in self.context.pages:
                for frame in page.frames:
                    if safe_count(frame.locator("#btn_s_confirm1")) or re.search("입금처조회", self.text_of(frame, 800)):
                        return Surface(page, frame)
            sleep(800)
        return None

    def payee_match_tokens(self) -> list[str]:
        supplier = self.get_invoice_supplier_info()
        row_tokens = [
            token
            for token in self.selected_invoice_row_text.split()
            if re.search("[가-힣]", token) and len(normalize_search_text(token)) >= 4
        ]
        tokens = unique([*supplier["tokens"], *row_tokens])
        if not tokens:
            raise RuntimeError("No supplier tokens were found from invoice PDF or selected invoice row")
        suffix = f" ({supplier['business_no']})" if supplier.get("business_no") else ""
        print(f"payee match tokens: {', '.join(tokens[:5])}{suffix}")
        return tokens

    def select_payee_result_row(self, payee_frame: Any, tokens: list[str]) -> str:
        selected_text = ""
        row_selected = False
        for _ in range(10):
            try:
                selected = payee_frame.evaluate(
                    """matchTokens => {
                        const normalize = value => String(value || "").toLowerCase().replace(/[\\s()[\\]{}.,/+_-]/g, "");
                        const normalizedTokens = matchTokens.map(normalize).filter(token => token.length >= 3);
                        let candidates = Array.from(document.querySelectorAll(".jgrid-row, tr"))
                            .filter(row => {
                                const text = normalize(row.innerText || row.textContent || "");
                                return normalizedTokens.some(token => text.includes(token));
                            });
                        if (!candidates.length) {
                            candidates = Array.from(document.querySelectorAll("td, span, a, div, li"))
                                .filter(element => {
                                    const text = normalize(element.innerText || element.textContent || "");
                                    return normalizedTokens.some(token => text.includes(token));
                                })
                                .map(element => element.closest(".jgrid-row, tr") || element)
                                .filter(Boolean);
                        }
                        candidates = Array.from(new Set(candidates)).sort((a, b) => {
                            const score = element => {
                                const raw = String(element.innerText || element.textContent || "");
                                const text = normalize(raw);
                                let value = 0;
                                for (const token of normalizedTokens) {
                                    if (/^\\d{10}$/.test(token) && text.includes(token)) value += 1000;
                                    else if (text.includes(token)) value += 100;
                                }
                                if (element.matches && element.matches(".jgrid-row")) value += 50;
                                if (element.querySelector && element.querySelector("input[type='checkbox'], input[type='radio']")) value += 40;
                                if (/은행|계좌|예금주|대표자|주소|사업자/.test(raw)) value += 20;
                                value += Math.min(raw.length, 400) / 100;
                                return value;
                            };
                            return score(b) - score(a);
                        });
                        const row = candidates[0];
                        if (!row) return {ok: false, text: ""};
                        row.scrollIntoView({block: "center", inline: "center"});
                        const checkbox = row.querySelector("input[type='checkbox'], input[type='radio']");
                        if (checkbox) {
                            if (!checkbox.checked) checkbox.click();
                            checkbox.checked = true;
                            checkbox.dispatchEvent(new Event("change", {bubbles: true}));
                        }
                        const grid = row.closest("table");
                        if (grid && row.id && window.jQuery && window.jQuery.fn && window.jQuery.fn.jqGrid) {
                            try { window.jQuery(grid).jqGrid("setSelection", row.id, true); } catch (error) {}
                        }
                        for (const element of [row]) {
                            element.dispatchEvent(new MouseEvent("mousedown", {bubbles: true, cancelable: true, view: window}));
                            element.dispatchEvent(new MouseEvent("mouseup", {bubbles: true, cancelable: true, view: window}));
                            element.dispatchEvent(new MouseEvent("click", {bubbles: true, cancelable: true, view: window}));
                            if (typeof element.click === "function") element.click();
                        }
                        return {ok: true, text: String(row.innerText || row.textContent || "").replace(/\\s+/g, " ").trim()};
                    }""",
                    tokens,
                )
                row_selected = bool(selected.get("ok"))
                selected_text = selected.get("text") or selected_text
            except PlaywrightError:
                row_selected = False
            if row_selected:
                break
            sleep(800)
        if not row_selected:
            self.dump("payee-search-no-row")
            raise RuntimeError("Payee result row was not found")
        sleep(800)
        if selected_text:
            print(f"payee candidate row: {selected_text[:180]}")
        return selected_text

    def set_payee_from_search(self) -> None:
        frame = self.general_claim_frame()
        self.click_first_visible(frame, "#btnRcvCd")
        sleep(3000)
        surface = self.payee_search_surface()
        if not surface:
            raise RuntimeError("Payee search surface was not found")
        payee_frame = surface.frame
        tokens = self.payee_match_tokens()
        self.select_payee_result_row(payee_frame, tokens)
        sleep(800)
        ok = payee_frame.locator("#btn_s_confirm1").first if safe_count(payee_frame.locator("#btn_s_confirm1")) else self.find_button_by_text_or_id(payee_frame, re.compile("확인|선택|적용|btn.*ok|btn.*confirm", re.I))
        self.dialog_messages.clear()
        ok.click(force=True, timeout=10000)
        sleep(3000)

        parent_frame = self.general_claim_frame()
        try:
            verified_name = parent_frame.locator("#RCV_NM:visible").first.input_value()
        except PlaywrightError:
            verified_name = ""
        if not verified_name and any("RCMS과제는 지급처명, 주소, 대표자명" in message for message in self.dialog_messages):
            print("RCMS payee validation requires vendor address/representative; updating vendor master")
            self.update_selected_payee_master(payee_frame)
            refreshed = self.payee_search_surface(8000)
            if refreshed:
                payee_frame = refreshed.frame
            self.select_payee_result_row(payee_frame, tokens)
            ok = payee_frame.locator("#btn_s_confirm1").first if safe_count(payee_frame.locator("#btn_s_confirm1")) else self.find_button_by_text_or_id(payee_frame, re.compile("확인|선택|적용|btn.*ok|btn.*confirm", re.I))
            self.dialog_messages.clear()
            ok.click(force=True, timeout=10000)
            sleep(3000)
            try:
                verified_name = parent_frame.locator("#RCV_NM:visible").first.input_value()
            except PlaywrightError:
                verified_name = ""
        if not verified_name:
            try:
                payee_frame.evaluate(
                    """matchTokens => {
                        const normalize = value => String(value || "").toLowerCase().replace(/[\\s()[\\]{}.,/+_-]/g, "");
                        const normalizedTokens = matchTokens.map(normalize).filter(token => token.length >= 3);
                        const rows = Array.from(document.querySelectorAll(".jgrid-row, tr"))
                            .filter(row => {
                                const text = normalize(row.innerText || row.textContent || "");
                                return normalizedTokens.some(token => text.includes(token));
                            })
                            .sort((a, b) => (b.innerText || b.textContent || "").length - (a.innerText || a.textContent || "").length);
                        const row = rows[0] || null;
                        if (!row) return false;
                        row.dispatchEvent(new MouseEvent("dblclick", {bubbles: true, cancelable: true, view: window}));
                        if (typeof row.dblclick === "function") row.dblclick();
                        return true;
                    }""",
                    tokens,
                )
            except PlaywrightError:
                pass
            sleep(2000)
        for _ in range(8):
            if verified_name:
                break
            sleep(800)
            try:
                verified_name = parent_frame.locator("#RCV_NM:visible").first.input_value()
            except PlaywrightError:
                verified_name = ""
        if not verified_name:
            self.dump("payee-not-applied")
            raise RuntimeError("Payee was selected in popup but was not applied to the claim form")
        normalized_verified = normalize_search_text(verified_name)
        if not any(normalize_search_text(token) in normalized_verified for token in tokens):
            self.dump("payee-mismatch")
            raise RuntimeError(f'Selected payee "{verified_name}" did not match invoice supplier tokens: {", ".join(tokens)}')
        print(f"payee selected: {verified_name}")

    def open_payee_search_and_select_row(self) -> Surface:
        frame = self.general_claim_frame()
        self.click_first_visible(frame, "#btnRcvCd")
        sleep(3000)
        surface = self.payee_search_surface()
        if not surface:
            raise RuntimeError("Payee search surface was not found")
        tokens = self.payee_match_tokens()
        self.select_payee_result_row(surface.frame, tokens)
        return surface

    def vendor_edit_surface(self, timeout: int = 15000) -> Surface | None:
        started = time.monotonic()
        pattern = re.compile(r"거래처\s*(등록|수정)|사업자등록번호|대표자|주소|계좌|은행")
        while (time.monotonic() - started) * 1000 < timeout:
            for page in self.context.pages:
                for frame in page.frames:
                    is_vendor_form = "rtask_0048_02" in frame.url or (
                        safe_count(frame.locator("#CUST_CD")) and safe_count(frame.locator("#CUST_NM"))
                    )
                    if is_vendor_form and safe_count(frame.locator("#btnSave, #btn_save")) and pattern.search(self.text_of(frame, 800)):
                        return Surface(page, frame)
            sleep(800)
        return None

    def editable_locator(self, frame: Any, selector: str) -> Any:
        locator = frame.locator(f"{selector}:visible").first
        if not safe_count(locator):
            locator = frame.locator(selector).first
        return locator

    def fill_vendor_field(self, frame: Any, selector: str, value: str, *, only_if_blank: bool = True) -> None:
        value = str(value or "").strip()
        if not value or not safe_count(frame.locator(selector)):
            return
        locator = self.editable_locator(frame, selector)
        try:
            current = str(locator.input_value() or "").strip()
        except PlaywrightError:
            current = ""
        if only_if_blank and current:
            print(f"vendor field kept {selector}: {current}")
            return
        locator.evaluate(
            """element => {
                element.disabled = false;
                element.readOnly = false;
                element.classList.remove("dis");
            }"""
        )
        locator.fill(value)
        locator.evaluate(
            """element => {
                element.dispatchEvent(new Event("input", {bubbles: true}));
                element.dispatchEvent(new Event("change", {bubbles: true}));
                element.dispatchEvent(new Event("blur", {bubbles: true}));
            }"""
        )
        print(f"vendor field set {selector}: {value}")

    def split_address_for_vendor(self, address: str) -> tuple[str, str]:
        address = re.sub(r"\s+", " ", address or "").strip()
        if not address:
            return "", ""
        match = re.match(r"(.+?\d+(?:-\d+)?),?\s*(.*)$", address)
        if match:
            return match.group(1).strip(), match.group(2).strip()
        return address, ""

    def fill_vendor_edit_form(self, frame: Any, info: dict[str, str]) -> None:
        base_address, detail_address = self.split_address_for_vendor(info.get("address", ""))
        postal_code = info.get("postal_code") or ""
        if postal_code and safe_count(frame.locator("#FIRST_CD1")) and safe_count(frame.locator("#FIRST_CD2")):
            self.fill_vendor_field(frame, "#FIRST_CD1", postal_code[:3])
            self.fill_vendor_field(frame, "#FIRST_CD2", postal_code[3:])
        self.fill_vendor_field(frame, "#CEO_NM", info.get("representative", ""))
        self.fill_vendor_field(frame, "#TEL_NO", info.get("phone", ""))
        self.fill_vendor_field(frame, "#EMAIL", info.get("email", ""))
        self.fill_vendor_field(frame, "#ADDR", base_address)
        self.fill_vendor_field(frame, "#ADDR_DTL", detail_address)

    def save_vendor_edit_form(self, surface: Surface) -> None:
        save = surface.frame.locator("#btnSave").first if safe_count(surface.frame.locator("#btnSave")) else self.find_button_by_text_or_id(surface.frame, re.compile("저장|save", re.I))
        self.dialog_messages.clear()
        save.click(force=True, timeout=10000)
        sleep(5000)
        failure_messages = [
            message
            for message in self.dialog_messages
            if re.search(r"확인하세요|입력하세요|선택하세요|등록되지|저장할\s*데이터가\s*없|오류|실패|불가", message)
        ]
        if failure_messages:
            self.dump("vendor-save-validation-failed")
            raise RuntimeError(f"Vendor save failed validation: {' / '.join(failure_messages)}")
        print("vendor master saved")

    def update_selected_payee_master(self, payee_frame: Any) -> None:
        info = self.supplier_registration_info()
        button = payee_frame.locator("#rbaseUpt").first if safe_count(payee_frame.locator("#rbaseUpt")) else self.find_button_by_text_or_id(payee_frame, re.compile("거래처수정|수정", re.I))
        button.click(force=True, timeout=10000)
        sleep(4000)
        edit = self.vendor_edit_surface()
        if not edit:
            self.dump("vendor-edit-form-not-found")
            raise RuntimeError("Vendor edit form was not found")
        self.fill_vendor_edit_form(edit.frame, info)
        self.save_vendor_edit_form(edit)
        try:
            edit.page.close()
        except PlaywrightError:
            pass
        sleep(1500)
        if safe_count(payee_frame.locator("#btn_search")):
            payee_frame.locator("#btn_search").first.click(force=True, timeout=10000)
            sleep(2500)
        print("vendor master update completed")

    def dump_vendor_edit_form(self) -> None:
        self.supplier_registration_info()
        surface = self.open_payee_search_and_select_row()
        frame = surface.frame
        button = frame.locator("#rbaseUpt").first if safe_count(frame.locator("#rbaseUpt")) else self.find_button_by_text_or_id(frame, re.compile("거래처수정|수정", re.I))
        button.click(force=True, timeout=10000)
        sleep(4000)
        edit = self.vendor_edit_surface()
        if not edit:
            self.dump("vendor-edit-form-not-found")
            raise RuntimeError("Vendor edit form was not found")
        self.dump("vendor-edit-form")

    def update_payee_vendor_master_from_current_case(self) -> None:
        self.prepare()
        surface = self.open_payee_search_and_select_row()
        self.update_selected_payee_master(surface.frame)

    def find_input_near_label(self, frame: Any, label_pattern: re.Pattern[str], fallback_ids: list[str] | None = None) -> Any:
        candidates = frame.locator("input[type='text']:visible, textarea:visible")
        matches = []
        for idx in range(safe_count(candidates)):
            item = candidates.nth(idx)
            try:
                score = item.evaluate(
                    """(element, source) => {
                        const re = new RegExp(source);
                        const row = element.closest("tr, li, div");
                        const text = row ? row.innerText : "";
                        const meta = [element.id, element.name, element.placeholder, element.title].join(" ");
                        if (element.disabled || element.readOnly) return null;
                        if (!re.test(`${text} ${meta}`)) return null;
                        return {
                            id: element.id || "",
                            name: element.name || "",
                            textLength: String(text || "").length,
                            exactLabel: re.test(String(text || "")),
                        };
                    }""",
                    label_pattern.pattern,
                )
            except PlaywrightError:
                score = None
            if score:
                matches.append((idx, score))
        if matches:
            matches.sort(key=lambda row: (not row[1].get("exactLabel"), row[1].get("textLength") or 9999))
            return candidates.nth(matches[0][0])
        for selector in fallback_ids or []:
            locator = frame.locator(selector).first
            if safe_count(locator):
                try:
                    if locator.is_visible() and not locator.evaluate("element => element.disabled || element.readOnly"):
                        return locator
                except PlaywrightError:
                    pass
        raise RuntimeError(f"Input near {label_pattern.pattern} was not found")

    def set_summary(self) -> None:
        frame = self.general_claim_frame()
        summary_input = self.find_input_near_label(
            frame,
            re.compile(r"적요"),
            ["#RMK", "#REMK", "#EXP_RMK", "#PAY_RMK", "#REQ_CONT", "#CONT"],
        )
        summary = self.summary_text()
        summary_input.fill(summary)
        summary_input.evaluate(
            """element => {
                element.dispatchEvent(new Event("input", {bubbles: true}));
                element.dispatchEvent(new Event("change", {bubbles: true}));
                element.dispatchEvent(new Event("blur", {bubbles: true}));
            }"""
        )
        sleep(500)
        try:
            applied = summary_input.input_value()
        except PlaywrightError:
            applied = ""
        if applied != summary:
            self.dump("summary-not-applied")
            raise RuntimeError(f'Summary was not applied. expected="{summary}", actual="{applied}"')
        print(f"summary set: {summary}")

    def set_inspection_today_and_self(self) -> None:
        frame = self.general_claim_frame()
        today_button = frame.locator("#btn_addCheckDate, a:has-text('[오늘]')").first
        if safe_count(today_button):
            today_button.click(force=True, timeout=10000)
            sleep(700)
        else:
            self.find_input_near_label(frame, re.compile("검수일|검수일자")).fill(date.today().isoformat())
        self_button = frame.locator("#btn_addUser, a:has-text('[본인]')").first
        if safe_count(self_button):
            self_button.click(force=True, timeout=10000)
            sleep(700)
        print("inspection date/user set")

    def set_rcms_extra_info(self) -> None:
        frame = self.general_claim_frame()
        selects = frame.locator("select:visible")
        changed: list[str] = []
        for idx in range(safe_count(selects)):
            select = selects.nth(idx)
            try:
                meta = select.evaluate(
                    """element => {
                        const row = element.closest("tr, li, div, table");
                        const options = Array.from(element.options || [])
                            .map(option => ({value: option.value || "", text: (option.textContent || "").trim()}));
                        return {
                            id: element.id || "",
                            name: element.name || "",
                            value: element.value || "",
                            nearby: (row ? row.innerText || row.textContent || "" : "").replace(/\\s+/g, " ").trim(),
                            options,
                            disabled: !!element.disabled
                        };
                    }"""
                )
            except PlaywrightError:
                continue
            if meta.get("disabled") or meta.get("value"):
                continue
            nearby = meta.get("nearby") or ""
            control_id = f"{meta.get('id') or ''} {meta.get('name') or ''}"
            if "RCMS 부가정보" not in nearby and not re.search(r"RCMS|USE_AMT|SELF|TRS|RMT|REMS", control_id, re.I):
                continue
            option = next(
                (
                    option
                    for option in meta.get("options", [])
                    if str(option.get("value") or "").strip()
                    and not re.fullmatch(r"\s*선택\s*", str(option.get("text") or ""))
                ),
                None,
            )
            if not option:
                continue
            select.select_option(option["value"])
            select.evaluate("""element => element.dispatchEvent(new Event("change", {bubbles: true}))""")
            changed.append(f"{meta.get('id') or meta.get('name')}: {option.get('text')} ({option.get('value')})")
            sleep(700)
        if changed:
            print("RCMS extra info selected: " + " / ".join(changed))

    def wait_any_surface(self, pattern: re.Pattern[str], timeout: int = 30000) -> Surface | None:
        started = time.monotonic()
        while (time.monotonic() - started) * 1000 < timeout:
            for page in self.context.pages:
                for frame in page.frames:
                    if pattern.search(self.text_of(frame, 800)):
                        return Surface(page, frame)
            sleep(800)
        return None

    def approval_line_surface(self, timeout: int = 30000) -> Surface | None:
        pattern = re.compile(r"신청\s*적요\s*구분\s*부서|구분\s*부서\s*본인|담당자\s*\(부서\).*확인|결재선", re.S)
        started = time.monotonic()
        while (time.monotonic() - started) * 1000 < timeout:
            for page in self.context.pages:
                for frame in page.frames:
                    if "rcomm_0043_01" in frame.url or "appr0043_13" in frame.url:
                        return Surface(page, frame)
                    title = ""
                    try:
                        title = page.title()
                    except PlaywrightError:
                        pass
                    text = self.text_of(frame, 800)
                    if re.search(r"신청|결재", title) and pattern.search(text):
                        return Surface(page, frame)
            sleep(800)
        return None

    def select_default_approval_line(self, frame: Any) -> dict[str, Any]:
        return frame.evaluate(
            """() => {
                const compact = value => String(value || "").replace(/\\s+/g, " ").trim();
                const visible = node => !!(node.offsetWidth || node.offsetHeight || node.getClientRects().length);
                const clickNode = node => {
                    if (!node) return false;
                    node.scrollIntoView({block: "center", inline: "center"});
                    node.dispatchEvent(new MouseEvent("mousedown", {bubbles: true, cancelable: true, view: window}));
                    node.dispatchEvent(new MouseEvent("mouseup", {bubbles: true, cancelable: true, view: window}));
                    node.dispatchEvent(new MouseEvent("click", {bubbles: true, cancelable: true, view: window}));
                    if (typeof node.click === "function") node.click();
                    return true;
                };
                const rowPattern = /김인수|과제담당자|담당자\\(부서\\)|본인/;
                const rows = Array.from(document.querySelectorAll("tr, li, div"))
                    .filter(row => visible(row) && rowPattern.test(compact(row.innerText || row.textContent || "")))
                    .sort((a, b) => compact(a.innerText || a.textContent || "").length - compact(b.innerText || b.textContent || "").length);
                const actions = [];
                for (const row of rows.slice(0, 6)) {
                    const rowText = compact(row.innerText || row.textContent || "");
                    const choice = row.querySelector("input[type='radio'], input[type='checkbox']");
                    if (choice) {
                        choice.checked = true;
                        choice.dispatchEvent(new Event("change", {bubbles: true}));
                        clickNode(choice);
                        actions.push({action: "choice", text: rowText.slice(0, 180)});
                    }
                    clickNode(row);
                    actions.push({action: "row", text: rowText.slice(0, 180)});
                    if (actions.length) return {ok: true, actions};
                }
                return {ok: false, actions};
            }"""
        )

    def click_approval_confirm(self, frame: Any) -> None:
        selectors = [
            "#btnConfirm",
            "#btn_confirm",
            "#btnOut",
            "#btn_out",
            "#btnOk",
            "#btn_ok",
            "button:has-text('확인')",
            "a:has-text('확인')",
            "input[type='button'][value*='확인']",
        ]
        for selector in selectors:
            locator = frame.locator(f"{selector}:visible").first
            if not safe_count(locator):
                continue
            try:
                locator.click(force=True, timeout=10000)
            except PlaywrightError:
                locator.evaluate("element => element.click()")
            return
        button = self.find_button_by_text_or_id(frame, re.compile(r"확인|btn.*confirm|btn.*ok|btn.*out", re.I))
        try:
            button.click(force=True, timeout=10000)
        except PlaywrightError:
            button.evaluate("element => element.click()")

    def confirm_approval_line(self, surface: Surface) -> None:
        frame = surface.frame
        self.dialog_messages.clear()
        selected = self.select_default_approval_line(frame)
        print(f"approval line selected: {json.dumps(selected, ensure_ascii=False)}")
        self.click_approval_confirm(frame)
        sleep(4000)
        if any(re.search(r"선택하세요|지정하세요|담당자", message) for message in self.dialog_messages):
            fallback = frame.evaluate(
                """() => {
                    const compact = value => String(value || "").replace(/\\s+/g, " ").trim();
                    const visible = node => !!(node.offsetWidth || node.offsetHeight || node.getClientRects().length);
                    const clickNode = node => {
                        if (!node) return false;
                        node.scrollIntoView({block: "center", inline: "center"});
                        node.dispatchEvent(new MouseEvent("mousedown", {bubbles: true, cancelable: true, view: window}));
                        node.dispatchEvent(new MouseEvent("mouseup", {bubbles: true, cancelable: true, view: window}));
                        node.dispatchEvent(new MouseEvent("click", {bubbles: true, cancelable: true, view: window}));
                        if (typeof node.click === "function") node.click();
                        return true;
                    };
                    const controls = Array.from(document.querySelectorAll("a, button, input[type='button'], span"))
                        .filter(node => visible(node) && /^선택$/.test(compact(node.innerText || node.value || node.title || "")));
                    const control = controls.find(node => /김인수|과제담당자|담당자|본인/.test(compact((node.closest("tr, li, div, table") || document.body).innerText || ""))) || controls[0];
                    return {
                        ok: clickNode(control),
                        text: control ? compact((control.closest("tr, li, div, table") || control).innerText || control.value || "").slice(0, 180) : ""
                    };
                }"""
            )
            print(f"approval select fallback: {json.dumps(fallback, ensure_ascii=False)}")
            sleep(2000)
            self.dialog_messages.clear()
            self.click_approval_confirm(frame)
            sleep(4000)

    def open_attach_dialog(self, frame: Any) -> None:
        button = frame.locator(
            ", ".join(
                [
                    "a:has-text('첨부')",
                    "button:has-text('첨부')",
                    "input[type='button'][value*='첨부']",
                    "a:has-text('원안문서첨부')",
                    "button:has-text('원안문서첨부')",
                ]
            )
        ).first
        if safe_count(button):
            button.click(force=True, timeout=10000)
            return
        raise RuntimeError("Attach button was not found")

    def upload_files_in_attach_dialog(self, surface: Surface, files: list[dict[str, Any]]) -> None:
        frame = surface.frame
        paths = [str(file["path"]) for file in files]
        input_locator = frame.locator("input[type='file']").first
        if safe_count(input_locator):
            input_locator.set_input_files(paths)
        else:
            file_button = frame.locator("#btn_file").first if safe_count(frame.locator("#btn_file")) else self.find_button_by_text_or_id(frame, re.compile("파일\\s*첨부|첨부|file", re.I))
            try:
                with surface.page.expect_file_chooser(timeout=10000) as chooser_info:
                    file_button.click(force=True, timeout=10000)
                chooser_info.value.set_files(paths)
            except PlaywrightError:
                raise
        sleep(1500)
        upload = frame.locator("#btn_upload").first if safe_count(frame.locator("#btn_upload")) else self.find_button_by_text_or_id(frame, re.compile("업로드|저장|확인|upload", re.I))
        upload.click(force=True, timeout=10000)
        sleep(5000)

    def attach_documents(self) -> None:
        self.validate_attachment_files()
        frame = self.general_claim_frame()
        self.open_attach_dialog(frame)
        sleep(1500)
        surface = self.wait_any_surface(re.compile(r"파일\s*업로드|파일\s*첨부|업로드"), 15000)
        if not surface:
            self.dump("attach-dialog-not-found")
            raise RuntimeError("Attach upload dialog was not found")
        self.upload_files_in_attach_dialog(surface, self.attachment_files())
        for file in self.attachment_files():
            print(f"attached {file['label']}: {file['path']}")

    def save_claim(self) -> None:
        frame = self.general_claim_frame()
        button = self.find_button_by_text_or_id(frame, re.compile(r"^.*(저장|btn.*save).*$", re.I))
        before_req_seq = self.request_sequence_text(frame)
        self.dialog_messages.clear()
        button.click(force=True, timeout=10000)
        sleep(5000)
        failure_messages = [
            message
            for message in self.dialog_messages
            if re.search(r"확인하세요|입력하세요|선택하세요|등록되지|저장할\s*데이터가\s*없|오류|실패|불가", message)
        ]
        if failure_messages:
            self.dump("save-validation-failed")
            raise RuntimeError(f"Save failed validation: {' / '.join(failure_messages)}")
        after_req_seq = self.request_sequence_text(self.general_claim_frame())
        if before_req_seq or after_req_seq:
            print(f"request sequence: {before_req_seq or '(blank)'} -> {after_req_seq or '(blank)'}")
        print("save clicked")

    def submit_current_claim(self) -> None:
        frame = self.general_claim_frame()
        button = frame.locator("#btn_apprProc:visible a:visible").first
        if not safe_count(button):
            button = frame.locator("#btn_apprProc:visible").first
        if not safe_count(button):
            button = self.find_button_by_text_or_id(frame, re.compile(r"(^|\\s)신청($|\\s)|btn_apprProc|apprProc", re.I))
        self.dialog_messages.clear()
        try:
            button.click(force=True, timeout=10000)
        except PlaywrightError:
            button.evaluate("element => element.click()")
        sleep(2000)
        approval = self.approval_line_surface(7000)
        if not approval:
            try:
                trigger_result = frame.evaluate(
                    """() => {
                        const span = document.querySelector("#btn_apprProc");
                        const anchor = document.querySelector("#btn_apprProc a");
                        if (window.jQuery) {
                            if (anchor) window.jQuery(anchor).trigger("click");
                            if (span) window.jQuery(span).trigger("click");
                            const events = span && window.jQuery._data ? window.jQuery._data(span, "events") : null;
                            const clicks = events && events.click ? events.click : [];
                            return clicks.map(item => {
                                try {
                                    const result = item.handler.call(span, window.jQuery.Event("click"));
                                    return {ok: true, result: String(result)};
                                } catch (error) {
                                    return {ok: false, error: String(error), stack: String(error && error.stack || "")};
                                }
                            });
                        }
                        return [];
                    }"""
                )
                if trigger_result:
                    print(f"submit trigger result: {json.dumps(trigger_result, ensure_ascii=False)}")
            except PlaywrightError:
                pass
            approval = self.approval_line_surface(15000)
        if approval:
            print(f"approval popup: {approval.page.title()} {approval.frame.url}")
            self.confirm_approval_line(approval)
        else:
            print("approval popup was not detected after submit click")
        self.wait_for_dialogs_to_settle(40000, 4000, 20000)
        failure_messages = [
            message
            for message in self.dialog_messages
            if re.search(r"확인하세요|입력하세요|선택하세요|등록되지|저장할\s*데이터가\s*없|오류|실패|불가|권한|반려|예산액을\s*초과|예산.*초과|미징수액|잔액", message)
        ]
        if failure_messages:
            if not any(re.search(r"예산액을\s*초과|예산.*초과|미징수액|잔액", message) for message in failure_messages):
                self.dump("submit-validation-failed")
            raise RuntimeError(f"Submit failed validation: {' / '.join(failure_messages)}")
        if self.dialog_messages:
            print(f"submit dialogs: {' / '.join(self.dialog_messages)}")
        print("submit clicked")

    def submit_existing_draft(self) -> None:
        self.go_to_research_portal()
        self.open_project()
        self.open_claim_tab()
        self.open_latest_draft_claim(self.options.draft_total)
        self.submit_current_claim()

    def request_sequence_text(self, frame: Any | None = None) -> str:
        try:
            frame = frame or self.general_claim_frame()
            locator = frame.locator("#REQ_SEQ_NO").first
            if not safe_count(locator):
                return ""
            if locator.evaluate("element => 'value' in element"):
                return str(locator.input_value() or "").strip()
            return re.sub(r"\s+", " ", locator.inner_text(timeout=1000)).strip()
        except PlaywrightError:
            return ""

    def open_evidence_registration(self) -> Surface:
        frame = self.general_claim_frame()
        self.click_first_visible(frame, "#btn_addEvid")
        sleep(3000)
        surface = self.purchase_evidence_surface()
        if not surface:
            self.dump("evidence-registration-not-found")
            raise RuntimeError("Evidence registration surface was not found")
        print(f"evidence registration opened: {surface.frame.url}")
        return surface

    def purchase_evidence_surface(self, timeout: int = 15000) -> Surface | None:
        started = time.monotonic()
        while (time.monotonic() - started) * 1000 < timeout:
            for page in self.context.pages:
                for frame in page.frames:
                    if safe_count(frame.locator("#btnExcelReg, #btnTaxBillReg, #btnConfirm")) or re.search("구매내역정보|구매등록내역|엑셀등록", self.text_of(frame, 800)):
                        return Surface(page, frame)
            sleep(800)
        return None

    def excel_registration_surface(self, timeout: int = 15000) -> Surface | None:
        started = time.monotonic()
        while (time.monotonic() - started) * 1000 < timeout:
            for page in self.context.pages:
                for frame in page.frames:
                    if safe_count(frame.locator("#btnOpenExcel, #btnSave")) or re.search(r"구매내역\s*엑셀등록|파일열기|양식다운로드", self.text_of(frame, 800)):
                        return Surface(page, frame)
            sleep(800)
        return None

    def excel_upload_mapping_surface(self, timeout: int = 15000) -> Surface | None:
        started = time.monotonic()
        while (time.monotonic() - started) * 1000 < timeout:
            for page in self.context.pages:
                for frame in page.frames:
                    if safe_count(frame.locator("#btn_save")) or re.search(r"데이터\s*매핑|업로드할\s*데이터|대상그리드|적용범위", self.text_of(frame, 800)):
                        return Surface(page, frame)
            sleep(800)
        return None

    def file_finder_surface(self, timeout: int = 15000) -> Surface | None:
        started = time.monotonic()
        while (time.monotonic() - started) * 1000 < timeout:
            for page in self.context.pages:
                for frame in page.frames:
                    if safe_count(frame.locator("input[type='file']")) or re.search(r"파일찾기|지원되는\s*파일형식", self.text_of(frame, 800)):
                        return Surface(page, frame)
            sleep(800)
        return None

    def upload_excel_in_registration_surface(self, surface: Surface, excel_path: Path) -> None:
        frame = surface.frame
        open_excel = frame.locator("#btnOpenExcel").first if safe_count(frame.locator("#btnOpenExcel")) else self.find_button_by_text_or_id(frame, re.compile("파일열기|엑셀|open", re.I))
        chooser = None
        try:
            with surface.page.expect_file_chooser(timeout=5000) as chooser_info:
                try:
                    open_excel.click(force=True, timeout=10000)
                except PlaywrightError:
                    open_excel.evaluate("element => element.click()")
            chooser = chooser_info.value
        except PlaywrightTimeoutError:
            chooser = None
        if chooser:
            chooser.set_files(str(excel_path))
        else:
            finder = self.file_finder_surface()
            if not finder:
                self.dump("excel-file-finder-not-found")
                raise RuntimeError("Excel file finder was not found")
            file_input = finder.frame.locator("input[type='file']").first
            if not safe_count(file_input):
                self.dump("excel-file-input-not-found")
                raise RuntimeError("Excel file input was not found")
            file_input.set_input_files(str(excel_path))
            ok = self.find_button_by_text_or_id(finder.frame, re.compile("확인|적용|선택|ok", re.I))
            try:
                ok.click(force=True, timeout=10000)
            except PlaywrightError:
                ok.evaluate("element => element.click()")

        mapping = self.excel_upload_mapping_surface(20000)
        if mapping:
            apply_button = mapping.frame.locator("#btn_save").first if safe_count(mapping.frame.locator("#btn_save")) else self.find_button_by_text_or_id(mapping.frame, re.compile("적용|apply", re.I))
            try:
                apply_button.click(force=True, timeout=10000)
            except PlaywrightError:
                apply_button.evaluate("element => element.click()")
            sleep(5000)
        else:
            sleep(5000)
        save_surface = self.excel_registration_surface(8000) or surface
        save = save_surface.frame.locator("#btnSave").first if safe_count(save_surface.frame.locator("#btnSave")) else self.find_button_by_text_or_id(save_surface.frame, re.compile("저장|save", re.I))
        try:
            save.click(force=True, timeout=10000)
        except PlaywrightError:
            save.evaluate("element => element.click()")
        sleep(5000)

    def register_purchase_items_excel(self) -> None:
        surface = self.purchase_evidence_surface(3000) or self.open_evidence_registration()
        frame = surface.frame
        excel_path = self.case_path("items.xls")
        if not excel_path.exists():
            raise RuntimeError(f"Missing purchase item excel: {excel_path}")
        excel_button = frame.locator("#btnExcelReg").first if safe_count(frame.locator("#btnExcelReg")) else self.find_button_by_text_or_id(frame, re.compile("엑셀등록|excel", re.I))
        chooser = None
        try:
            with surface.page.expect_file_chooser(timeout=3000) as chooser_info:
                try:
                    excel_button.click(force=True, timeout=10000)
                except PlaywrightError:
                    excel_button.evaluate("element => element.click()")
            chooser = chooser_info.value
        except PlaywrightTimeoutError:
            chooser = None
        if chooser:
            chooser.set_files(str(excel_path))
            sleep(5000)
        else:
            excel_surface = self.excel_registration_surface()
            if not excel_surface:
                self.dump("excel-registration-surface-not-found")
                raise RuntimeError("Excel registration surface was not found")
            self.upload_excel_in_registration_surface(excel_surface, excel_path)
        sleep(5000)
        updated = self.purchase_evidence_surface(5000) or surface
        text = self.text_of(updated.frame, 1200)
        if re.search(r"현재\s*0\s*건|총\s*0\s*건", text):
            self.dump("purchase-items-not-registered")
            raise RuntimeError("Purchase item excel upload did not register any rows")
        confirm = updated.frame.locator("#btnConfirm").first if safe_count(updated.frame.locator("#btnConfirm")) else self.find_button_by_text_or_id(updated.frame, re.compile("확인|적용|선택"))
        try:
            confirm.click(force=True, timeout=10000)
        except PlaywrightError:
            confirm.evaluate("element => element.click()")
        sleep(3000)
        print(f"purchase item excel registered: {excel_path}")

    def prepare(self) -> None:
        self.go_to_research_portal()
        self.open_project()
        self.open_claim_tab()
        self.open_general_claim()
        self.prepare_current_line_item()

    def prepare_current_line_item(self) -> None:
        self.classify_case_documents()
        self.select_budget_and_receipt()
        self.open_invoice_selector()
        self.search_invoice_by_mail(self.options.invoice_mail)
        self.select_first_invoice_result()
        self.set_claim_kind_consumable()

    def fill_and_save_current_line_item(self) -> None:
        self.reset_detail_form_for_add()
        self.prepare_current_line_item()
        self.set_payee_from_search()
        self.set_summary()
        self.set_inspection_today_and_self()
        self.set_rcms_extra_info()
        self.attach_documents()
        self.register_purchase_items_excel()
        self.save_claim()

    def fill_and_save_purchase_claim(self) -> None:
        case_dirs = [Path(path) for path in (self.options.case_dirs or [DEFAULT_CASE_DIR])]
        self.preflight_case_dirs(case_dirs)
        self.go_to_research_portal()
        self.open_project()
        self.open_claim_tab()
        self.open_general_claim()
        for index, case_dir in enumerate(case_dirs, 1):
            self.set_case_dir(case_dir)
            print(f"\n=== line item {index}/{len(case_dirs)}: {self.case_path()} ===")
            self.fill_and_save_current_line_item()

    def fill_and_submit_purchase_claim(self) -> None:
        self.fill_and_save_purchase_claim()
        self.submit_current_claim()

    def append_to_existing_draft(self) -> None:
        case_dirs = [Path(path) for path in (self.options.case_dirs or [DEFAULT_CASE_DIR])]
        self.preflight_case_dirs(case_dirs)
        self.go_to_research_portal()
        self.open_project()
        self.open_claim_tab()
        self.open_latest_draft_claim(self.options.draft_total)
        for index, case_dir in enumerate(case_dirs, 1):
            self.set_case_dir(case_dir)
            print(f"\n=== append line item {index}/{len(case_dirs)}: {self.case_path()} ===")
            self.fill_and_save_current_line_item()

    def preflight_case_dirs(self, case_dirs: list[Path]) -> None:
        original_case_dir = self.case_dir
        for index, case_dir in enumerate(case_dirs, 1):
            self.set_case_dir(case_dir)
            print(f"\n=== preflight {index}/{len(case_dirs)}: {self.case_path()} ===")
            self.classify_case_documents()
            info = self.get_purchase_items_info()
            supplier = self.get_invoice_supplier_info()
            print(f"items total: {format_money(info['total'])}원")
            if supplier.get("business_no"):
                print(f"invoice supplier: {supplier.get('name') or '(name unknown)'} {supplier.get('business_no')}")
        self.set_case_dir(original_case_dir)

    def dump(self, label: str = "status") -> None:
        print(f"\n=== {label} ===")
        for page in self.context.pages:
            print(f"page: {page.title()} {page.url}")
            for frame in page.frames:
                text = self.text_of(frame, 800)
                if re.search("202403110003|청구|일반청구|세금계산서|계산서|연구재료|소모성|수신메일|메일|sheep|공급|사업자|조회|첨부|문서|저장|확인|지급처|입금처|거래처", text):
                    print(f"frame: {frame.name or 'main'} {frame.url[:180]}")
                    print(text[:1600])
                    if (
                        "rcomm_0033_01" in frame.url
                        or "rcv_pop" in frame.url
                        or "rtask_0048_02" in frame.url
                        or safe_count(frame.locator("#TAX_RCV_EMAIL_V"))
                        or safe_count(frame.locator("#PAY_EXP_CD"))
                    ):
                        try:
                            controls = frame.locator("input, select, button, a").evaluate_all(
                                """nodes => nodes.map((node, index) => {
                                    const row = node.closest("tr, li, div, table");
                                    return {
                                        index,
                                        tag: node.tagName,
                                        type: node.type || "",
                                        id: node.id || "",
                                        name: node.name || "",
                                        value: node.value || "",
                                        text: (node.innerText || node.value || node.title || node.placeholder || "").trim().replace(/\\s+/g, " ").slice(0, 120),
                                        nearby: (row ? row.innerText : "").trim().replace(/\\s+/g, " ").slice(0, 220),
                                        visible: !!(node.offsetWidth || node.offsetHeight || node.getClientRects().length),
                                        checked: !!node.checked,
                                        disabled: !!node.disabled,
                                        readOnly: !!node.readOnly
                                    };
                                }).filter(item => item.visible || /TAX|BIZ|EMAIL|MAIL|btn|S_/.test(JSON.stringify(item))).slice(0, 160)"""
                            )
                            print(json.dumps(controls, ensure_ascii=False, indent=2))
                        except PlaywrightError:
                            pass

    def dump_actions(self) -> None:
        frame = self.general_claim_frame()
        result = frame.evaluate(
            """() => {
                const compact = value => String(value || "").replace(/\\s+/g, " ").trim();
                const actionNodes = Array.from(document.querySelectorAll("a, button, input[type='button'], input[type='submit'], img"))
                    .map((node, index) => {
                        const row = node.closest("tr, li, div, table");
                        const onclick = node.getAttribute("onclick") || "";
                        const href = node.getAttribute("href") || "";
                        return {
                            index,
                            tag: node.tagName,
                            id: node.id || "",
                            name: node.name || "",
                            type: node.type || "",
                            text: compact(node.innerText || node.value || node.alt || node.title || ""),
                            title: node.title || "",
                            className: node.className || "",
                            onclick: compact(onclick).slice(0, 220),
                            href: compact(href).slice(0, 220),
                            nearby: compact(row ? row.innerText : "").slice(0, 260),
                            visible: !!(node.offsetWidth || node.offsetHeight || node.getClientRects().length),
                        };
                    })
                    .filter(item => item.visible || /btn|save|add|ins|grid|list|detail|evid|req|청구|내역|저장|추가|등록/i.test(JSON.stringify(item)));

                const idNodes = Array.from(document.querySelectorAll("[id]"))
                    .map(node => ({
                        tag: node.tagName,
                        id: node.id,
                        name: node.name || "",
                        type: node.type || "",
                        value: compact(node.value || node.innerText || "").slice(0, 120),
                        visible: !!(node.offsetWidth || node.offsetHeight || node.getClientRects().length),
                    }))
                    .filter(item => /btn|save|add|ins|grid|list|detail|dtl|evid|req|pay|exp|slip|resol|resul|manus|청구|내역|저장|추가|등록/i.test(JSON.stringify(item)));

                const scripts = Array.from(document.scripts).map(script => script.textContent || "").join("\\n");
                const patterns = ["내역", "청구내역", "저장", "add", "Add", "insert", "Insert", "save", "Save", "dtl", "Dtl", "detail", "Detail", "grid", "Grid"];
                const snippets = [];
                for (const pattern of patterns) {
                    let start = 0;
                    while (snippets.length < 80) {
                        const index = scripts.indexOf(pattern, start);
                        if (index < 0) break;
                        snippets.push(compact(scripts.slice(Math.max(0, index - 160), index + 260)));
                        start = index + pattern.length;
                    }
                }
                return {actions: actionNodes, ids: idNodes.slice(0, 260), snippets};
            }"""
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))

    def dump_claim_list_actions(self) -> None:
        frame = self.claim_list_frame()
        result = frame.evaluate(
            """() => {
                const compact = value => String(value || "").replace(/\\s+/g, " ").trim();
                const grids = Array.from(document.querySelectorAll("table[id]")).map(table => {
                    let ids = [];
                    let rows = [];
                    if (window.jQuery && window.jQuery.fn && window.jQuery.fn.jqGrid) {
                        try { ids = window.jQuery(table).jqGrid("getDataIDs") || []; } catch (error) { ids = []; }
                        rows = ids.map(id => {
                            let data = {};
                            try { data = window.jQuery(table).jqGrid("getRowData", id) || {}; } catch (error) { data = {}; }
                            const node = document.getElementById(id);
                            return {id, data, text: compact(node ? node.innerText || node.textContent || "" : "")};
                        });
                    }
                    return {id: table.id, className: table.className, ids, rows: rows.slice(0, 12)};
                }).filter(grid => grid.ids.length || /grid|list|jq/i.test(`${grid.id} ${grid.className}`));
                const actions = Array.from(document.querySelectorAll("a, button, input[type='button'], span"))
                    .map((node, index) => ({
                        index,
                        tag: node.tagName,
                        id: node.id || "",
                        className: node.className || "",
                        text: compact(node.innerText || node.value || node.title || ""),
                        onclick: compact(node.getAttribute("onclick") || "").slice(0, 300),
                        href: compact(node.getAttribute("href") || "").slice(0, 180),
                        visible: !!(node.offsetWidth || node.offsetHeight || node.getClientRects().length),
                    }))
                    .filter(item => /상세|변경|삭제|파일|복사|청구|결의|조회|btn|detail|mod|save/i.test(JSON.stringify(item)));
                const amountElements = Array.from(document.querySelectorAll("body *"))
                    .filter(node => compact(node.innerText || node.textContent || "").includes("2,948,000"))
                    .sort((a, b) => compact(a.innerText || a.textContent || "").length - compact(b.innerText || b.textContent || "").length)
                    .slice(0, 30)
                    .map(node => ({
                        tag: node.tagName,
                        id: node.id || "",
                        className: node.className || "",
                        text: compact(node.innerText || node.textContent || "").slice(0, 300),
                        html: compact(node.outerHTML || "").slice(0, 500),
                        visible: !!(node.offsetWidth || node.offsetHeight || node.getClientRects().length),
                    }));
                const rows = Array.from(document.querySelectorAll(".jgrid-row"))
                    .map(row => compact(row.innerText || row.textContent || ""))
                    .filter(Boolean)
                    .slice(0, 20);
                const scripts = Array.from(document.scripts).map(script => script.textContent || "").join("\\n");
                const snippets = [];
                for (const pattern of ["상세조회", "선택된 목록", "btn", "jqGrid", "selrow", "getGridParam", "REQ", "RESOL"]) {
                    let start = 0;
                    while (snippets.length < 80) {
                        const index = scripts.indexOf(pattern, start);
                        if (index < 0) break;
                        snippets.push(compact(scripts.slice(Math.max(0, index - 220), index + 360)));
                        start = index + pattern.length;
                    }
                }
                return {grids, actions, rows, amountElements, snippets};
            }"""
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))

    def dump_submit_actions(self) -> None:
        frame = self.general_claim_frame()
        result = frame.evaluate(
            """() => {
                const compact = value => String(value || "").replace(/\\s+/g, " ").trim();
                const keywords = /신청|제출|상신|결재|승인|접수|완료|appl|apply|submit|approval|gw|btn/i;
                const actions = Array.from(document.querySelectorAll("a, button, input[type='button'], input[type='submit'], span, img"))
                    .map((node, index) => {
                        const row = node.closest("tr, li, div, table");
                        const text = compact(node.innerText || node.value || node.alt || node.title || "");
                        const meta = compact([
                            node.id || "",
                            node.name || "",
                            node.className || "",
                            text,
                            node.getAttribute("onclick") || "",
                            node.getAttribute("href") || "",
                            row ? row.innerText || row.textContent || "" : ""
                        ].join(" "));
                        return {
                            index,
                            tag: node.tagName,
                            id: node.id || "",
                            name: node.name || "",
                            type: node.type || "",
                            text,
                            title: node.title || "",
                            className: node.className || "",
                            onclick: compact(node.getAttribute("onclick") || "").slice(0, 500),
                            href: compact(node.getAttribute("href") || "").slice(0, 220),
                            nearby: compact(row ? row.innerText || row.textContent || "" : "").slice(0, 360),
                            visible: !!(node.offsetWidth || node.offsetHeight || node.getClientRects().length),
                            meta,
                        };
                    })
                    .filter(item => keywords.test(item.meta));

                const ids = Array.from(document.querySelectorAll("[id]"))
                    .map(node => ({
                        tag: node.tagName,
                        id: node.id || "",
                        name: node.name || "",
                        type: node.type || "",
                        value: compact(node.value || node.innerText || node.textContent || "").slice(0, 180),
                        visible: !!(node.offsetWidth || node.offsetHeight || node.getClientRects().length),
                    }))
                    .filter(item => keywords.test(JSON.stringify(item)));

                const scripts = Array.from(document.scripts).map(script => script.textContent || "").join("\\n");
                const patterns = ["신청", "제출", "상신", "결재", "승인", "appl", "Appl", "submit", "Submit", "approval", "Approval", "gw", "GW", "btn"];
                const snippets = [];
                for (const pattern of patterns) {
                    let start = 0;
                    while (snippets.length < 120) {
                        const index = scripts.indexOf(pattern, start);
                        if (index < 0) break;
                        snippets.push(compact(scripts.slice(Math.max(0, index - 260), index + 520)));
                        start = index + pattern.length;
                    }
                }
                const submitTargets = Array.from(document.querySelectorAll("#btn_apprProc, #btn_apprProc *, [id*='appr' i], [id*='Appr'], [onclick*='appr' i], [onclick*='신청']"))
                    .map((node, index) => {
                        const row = node.closest("tr, li, div, table");
                        return {
                            index,
                            tag: node.tagName,
                            id: node.id || "",
                            name: node.name || "",
                            type: node.type || "",
                            text: compact(node.innerText || node.value || node.alt || node.title || ""),
                            className: node.className || "",
                            onclick: compact(node.getAttribute("onclick") || "").slice(0, 800),
                            href: compact(node.getAttribute("href") || "").slice(0, 300),
                            outerHTML: compact(node.outerHTML || "").slice(0, 900),
                            nearby: compact(row ? row.innerText || row.textContent || "" : "").slice(0, 500),
                            visible: !!(node.offsetWidth || node.offsetHeight || node.getClientRects().length),
                        };
                    });
                const jqueryEvents = [];
                if (window.jQuery && window.jQuery._data) {
                    for (const target of [document, document.body, document.querySelector("#btn_apprProc"), document.querySelector("#btn_apprProc a")].filter(Boolean)) {
                        const events = window.jQuery._data(target, "events") || {};
                        for (const [type, handlers] of Object.entries(events)) {
                            for (const handler of handlers || []) {
                                jqueryEvents.push({
                                    target: target === document ? "document" : target === document.body ? "body" : (target.id || target.tagName),
                                    type,
                                    namespace: handler.namespace || "",
                                    selector: handler.selector || "",
                                    handler: String(handler.handler || "").replace(/\\s+/g, " ").slice(0, 1600),
                                });
                            }
                        }
                    }
                }
                const scriptSources = Array.from(document.scripts)
                    .map(script => script.src || "")
                    .filter(Boolean);
                return {url: location.href, title: document.title, scriptSources, submitTargets, jqueryEvents, actions, ids: ids.slice(0, 300), snippets};
            }"""
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))

    def run_step(self, step: str) -> None:
        if step == "login":
            self.ensure_portal_login()
        elif step == "t09":
            self.go_to_research_portal()
        elif step == "project":
            self.open_project()
        elif step == "claim":
            self.open_claim_tab()
        elif step == "general":
            self.open_general_claim()
        elif step == "basic":
            self.select_budget_and_receipt()
        elif step == "invoice":
            self.open_invoice_selector()
        elif step == "mail":
            self.search_invoice_by_mail(self.options.invoice_mail)
        elif step == "select-invoice":
            self.select_first_invoice_result()
        elif step == "kind":
            self.set_claim_kind_consumable()
        elif step == "payee":
            self.set_payee_from_search()
        elif step == "detail":
            self.set_summary()
        elif step == "inspect":
            self.set_inspection_today_and_self()
        elif step == "attach":
            self.attach_documents()
        elif step == "evid":
            self.open_evidence_registration()
        elif step == "items":
            self.register_purchase_items_excel()
        elif step == "save":
            self.save_claim()
        elif step == "fill-save":
            self.fill_and_save_purchase_claim()
        elif step == "fill-submit":
            self.fill_and_submit_purchase_claim()
        elif step == "preflight":
            self.preflight_case_dirs([Path(path) for path in (self.options.case_dirs or [DEFAULT_CASE_DIR])])
        elif step == "prepare":
            self.prepare()
        elif step == "dump":
            self.dump("dump")
        elif step == "actions":
            self.dump_actions()
        elif step == "append-draft":
            self.append_to_existing_draft()
        elif step == "list-actions":
            self.go_to_research_portal()
            self.open_project()
            self.open_claim_tab()
            self.dump_claim_list_actions()
        elif step == "vendor-dump":
            self.prepare()
            self.dump_vendor_edit_form()
        elif step == "vendor-update":
            self.update_payee_vendor_master_from_current_case()
        elif step == "submit-actions":
            self.go_to_research_portal()
            self.open_project()
            self.open_claim_tab()
            self.open_latest_draft_claim(self.options.draft_total)
            self.dump_submit_actions()
        elif step == "submit-draft":
            self.submit_existing_draft()
        elif step == "latest":
            self.go_to_research_portal()
            self.open_project()
            self.open_claim_tab()
            self.open_latest_draft_claim(self.options.draft_total)
        else:
            raise RuntimeError(f"Unknown step: {step}")

    def interactive(self) -> None:
        print("Interactive commands:")
        print("  login, t09, project, claim, general, basic, invoice, mail, select-invoice, kind, payee, detail, inspect, attach, evid, items, save, fill-save, append-draft, preflight, prepare, dump, actions, list-actions, vendor-dump, vendor-update, submit-actions, submit-draft, fill-submit, quit")
        for line in sys.stdin:
            command = line.strip()
            if not command:
                continue
            if command == "quit":
                self.close()
                return
            try:
                self.run_step(command)
                print(f"done: {command}")
            except Exception as error:
                print(f"error: {command}", file=sys.stderr)
                print(error, file=sys.stderr)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(line_buffering=True)
    options = parse_args()
    apply_project_config(options)
    uploader = PortalUploader(options)
    if options.step == "preflight" and not options.interactive:
        uploader.run_step(options.step)
        return
    uploader.start()
    if options.interactive:
        uploader.interactive()
        return
    try:
        uploader.run_step(options.step)
    finally:
        uploader.close()


if __name__ == "__main__":
    main()
