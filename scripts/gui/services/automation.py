from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .paths import AUTOMATION_SETTINGS, AUTOMATION_STATE, ensure_gui_dirs


ACTIONS = ("collect_docs", "generate_purchase_docs", "upload_purchases", "process_receipts", "send_meeting_mail")
ACTION_LABELS = {
    "collect_docs": "Collect Docs",
    "generate_purchase_docs": "Generate Purchase Docs",
    "upload_purchases": "Upload Purchases",
    "process_receipts": "Process Receipts",
    "send_meeting_mail": "Send mail",
}


def default_action_settings() -> dict[str, object]:
    return {
        "dailyEnabled": False,
        "dailyHour": 9,
        "monthlyEnabled": False,
        "monthlyDay": 1,
    }


def default_settings() -> dict[str, object]:
    return {
        "timezone": "UTC",
        "monthlyHour": 0,
        "defaultProjectId": "",
        "visibleProjectIds": [],
        "meetingEmailRecipient": "sheepvs5@gmail.com",
        "notificationEmailRecipient": "",
        "actions": {action: default_action_settings() for action in ACTIONS},
    }


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def bounded_int(value: object, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return min(maximum, max(minimum, parsed))


def sanitize_settings(raw: dict[str, Any]) -> dict[str, object]:
    settings = default_settings()
    settings["defaultProjectId"] = str(raw.get("defaultProjectId") or "")
    raw_visible = raw.get("visibleProjectIds")
    settings["visibleProjectIds"] = [str(value) for value in raw_visible] if isinstance(raw_visible, list) else []
    settings["meetingEmailRecipient"] = str(raw.get("meetingEmailRecipient") or settings["meetingEmailRecipient"])
    settings["notificationEmailRecipient"] = str(raw.get("notificationEmailRecipient") or "")
    raw_actions = raw.get("actions") if isinstance(raw.get("actions"), dict) else {}
    actions: dict[str, object] = {}
    for action in ACTIONS:
        raw_action = raw_actions.get(action) if isinstance(raw_actions, dict) else {}
        raw_action = raw_action if isinstance(raw_action, dict) else {}
        actions[action] = {
            "dailyEnabled": bool(raw_action.get("dailyEnabled", False)),
            "dailyHour": bounded_int(raw_action.get("dailyHour"), 9, 0, 23),
            "monthlyEnabled": bool(raw_action.get("monthlyEnabled", False)),
            "monthlyDay": bounded_int(raw_action.get("monthlyDay"), 1, 1, 28),
        }
    settings["actions"] = actions
    return settings


def read_settings() -> dict[str, object]:
    ensure_gui_dirs()
    return sanitize_settings(read_json(AUTOMATION_SETTINGS))


def write_settings(settings: dict[str, Any]) -> dict[str, object]:
    sanitized = sanitize_settings(settings)
    write_json(AUTOMATION_SETTINGS, sanitized)
    return sanitized


def read_state() -> dict[str, Any]:
    ensure_gui_dirs()
    state = read_json(AUTOMATION_STATE)
    runs = state.get("runs")
    if not isinstance(runs, dict):
        state["runs"] = {}
    return state


def write_state(state: dict[str, Any]) -> None:
    write_json(AUTOMATION_STATE, state)


def due_actions(now: datetime | None = None) -> list[tuple[str, str, str]]:
    now = now or datetime.now(timezone.utc)
    settings = read_settings()
    state = read_state()
    runs = state.get("runs") if isinstance(state.get("runs"), dict) else {}
    result: list[tuple[str, str, str]] = []
    actions = settings.get("actions") if isinstance(settings.get("actions"), dict) else {}
    for action in ACTIONS:
        config = actions.get(action)
        if not isinstance(config, dict):
            continue
        if config.get("dailyEnabled") and int(config.get("dailyHour") or 0) == now.hour:
            key = f"daily:{action}:{now:%Y-%m-%d}:{now.hour:02d}"
            if key not in runs:
                result.append((action, "daily", key))
        if config.get("monthlyEnabled") and int(config.get("monthlyDay") or 1) == now.day and now.hour == 0:
            key = f"monthly:{action}:{now:%Y-%m}:{now.day:02d}"
            if key not in runs:
                result.append((action, "monthly", key))
    return result


def record_run(action: str, schedule: str, key: str, *, ok: bool, detail: str = "") -> None:
    state = read_state()
    runs = state.setdefault("runs", {})
    if not isinstance(runs, dict):
        runs = {}
        state["runs"] = runs
    runs[key] = {
        "action": action,
        "schedule": schedule,
        "ok": ok,
        "detail": detail,
        "recordedAt": datetime.now(timezone.utc).isoformat(),
    }
    write_state(state)
