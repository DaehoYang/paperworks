from __future__ import annotations

import math
from pathlib import Path

import yaml

from .models import Member
from .paths import INFO_YML


DEFAULT_TOPICS = {
    "quantum": ("양자광학 정기 랩미팅", ["최근 양자광학 연구 진행 상황 공유", "앞으로의 연구 방향 논의", "논문 작성 검토"]),
    "holography": ("홀로그래피 정기 랩미팅", ["최근 홀로그래피 연구 진행 상황 공유", "앞으로의 연구 방향 논의", "논문 작성 검토"]),
    "deeplearning": ("딥러닝 연구 정기 랩미팅", ["최근 딥러닝 응용 연구 진행 상황 공유", "앞으로의 연구 방향 논의", "논문 작성 검토"]),
}


def read_config(path: Path = INFO_YML) -> dict[str, object]:
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    return data


def project_info() -> dict[str, str]:
    project = read_config().get("project", {})
    if not isinstance(project, dict):
        return {}
    return {str(key): str(value) for key, value in project.items() if value is not None}


def trip_info() -> dict[str, str]:
    defaults = {
        "principal_department": "",
        "principal_name": project_info().get("연구책임자", ""),
        "traveler_name": "양대호",
        "participation": "참여연구원",
        "birthdate": "",
        "account": "",
    }
    raw = read_config().get("trip", {})
    if not isinstance(raw, dict):
        return defaults
    principal = raw.get("principal_investigator", {})
    traveler = raw.get("traveler", {})
    if not isinstance(principal, dict):
        principal = {}
    if not isinstance(traveler, dict):
        traveler = {}
    return {
        **defaults,
        "principal_department": str(principal.get("department") or defaults["principal_department"]),
        "principal_name": str(principal.get("name") or defaults["principal_name"]),
        "traveler_name": str(traveler.get("name") or defaults["traveler_name"]),
        "participation": str(traveler.get("participation") or defaults["participation"]),
        "birthdate": str(traveler.get("birthdate") or defaults["birthdate"]),
        "account": str(traveler.get("account") or defaults["account"]),
    }


def members() -> list[Member]:
    raw_members = read_config().get("members", [])
    if not isinstance(raw_members, list):
        raise ValueError("information.yml field 'members' must be a list")
    result: list[Member] = []
    for raw in raw_members:
        if not isinstance(raw, dict) or not raw.get("name"):
            continue
        result.append(
            Member(
                department=str(raw.get("department") or "물리학과"),
                position=str(raw.get("position") or "연구원"),
                name=str(raw["name"]),
            )
        )
    return result


def member_by_name(name: str) -> Member | None:
    return next((member for member in members() if member.name == name), None)


def attendee_rules() -> dict[str, object]:
    defaults = {
        "price_per_person": 30000,
        "min_attendees": 2,
        "max_attendees": 10,
        "fixed_first_attendee": "양대호",
        "max_attendee_store_exceptions": [
            {"store_name_contains": "쩡이네", "min_total_price": 100000},
            {"store_name_contains": "쟁이네", "min_total_price": 100000},
            {"store_name_contains": "평이네", "min_total_price": 100000},
        ],
    }
    rules = read_config().get("attendee_rules", {})
    return {**defaults, **rules} if isinstance(rules, dict) else defaults


def meeting_places() -> dict[str, str]:
    defaults = {
        "gachon_univ": "바이오나노연구원 315호",
        "seoul_west": "이화여자대학교 의과대학 1109호",
        "ewha_mokdong": "이대목동병원 MCC A 지하 1층 세미나실",
        "kriss": "한국표준과학연구원 313동 1층 회의공간",
    }
    places = read_config().get("meeting_places", {})
    return {**defaults, **{str(key): str(value) for key, value in places.items()}} if isinstance(places, dict) else defaults


def districts(key: str) -> tuple[str, ...]:
    value = read_config().get(key, [])
    if not isinstance(value, list):
        return ()
    return tuple(str(item) for item in value if item)


def derive_meeting_place(address: str, store_name: str) -> str:
    places = meeting_places()
    order = [
        ("gachon_univ", "gachon_univ_districts"),
        ("ewha_mokdong", "ewha_mokdong_districts"),
        ("seoul_west", "seoul_west_districts"),
        ("kriss", "kriss_districts"),
    ]
    for place_key, district_key in order:
        if any(token in address for token in districts(district_key)):
            return places[place_key]
    return store_name


def parse_topics(section: str) -> dict[str, tuple[str, list[str]]]:
    raw_topics = read_config().get(section, {})
    if not isinstance(raw_topics, dict):
        return {}
    result: dict[str, tuple[str, list[str]]] = {}
    for key, value in raw_topics.items():
        if not isinstance(value, dict):
            continue
        title = value.get("title")
        content = value.get("content")
        if title and isinstance(content, list):
            result[str(key)] = (str(title), [str(line) for line in content])
    return result


def topics() -> dict[str, tuple[str, list[str]]]:
    return parse_topics("topics") or DEFAULT_TOPICS


def external_topics() -> dict[str, tuple[str, list[str]]]:
    return parse_topics("external_topics")


def all_topics() -> dict[str, tuple[str, list[str]]]:
    return {**topics(), **external_topics()}


def topic_order(external: bool = False) -> tuple[str, ...]:
    section = "external_topic_order" if external else "topic_order"
    available = external_topics() if external else topics()
    raw_order = read_config().get(section, [])
    if isinstance(raw_order, list):
        cleaned = tuple(str(key) for key in raw_order if str(key) in available)
        if cleaned:
            return cleaned
    return tuple(available)


def external_place_keys() -> set[str]:
    keys: set[str] = set()
    raw_members = read_config().get("external_members", [])
    if isinstance(raw_members, list):
        for raw in raw_members:
            if isinstance(raw, dict) and isinstance(raw.get("meeting_places"), list):
                keys.update(str(place) for place in raw["meeting_places"])
    return keys


def meeting_place_key(meeting_place: str) -> str | None:
    for key, value in meeting_places().items():
        if value == meeting_place:
            return key
    return None


def external_members_for_place(meeting_place: str) -> list[Member]:
    place_key = meeting_place_key(meeting_place)
    if not place_key:
        return []
    result: list[Member] = []
    raw_members = read_config().get("external_members", [])
    if not isinstance(raw_members, list):
        return []
    for raw in raw_members:
        if not isinstance(raw, dict) or not raw.get("name"):
            continue
        places = raw.get("meeting_places", [])
        if isinstance(places, list) and place_key in [str(place) for place in places]:
            result.append(Member(str(raw.get("department") or ""), str(raw.get("position") or ""), str(raw["name"])))
    return result


def max_attendee_store_exception(total_price: int, store_name: str, rules: dict[str, object]) -> bool:
    exceptions = rules.get("max_attendee_store_exceptions", [])
    if not isinstance(exceptions, list):
        return False
    for raw in exceptions:
        if not isinstance(raw, dict):
            continue
        token = str(raw.get("store_name_contains") or "")
        threshold = int(raw.get("min_total_price") or 0)
        if token and token in store_name and total_price >= threshold:
            return True
    return False


def attendee_count(total_price: int, item_count: int | None, food_count: int | None, drink_count: int | None, store_name: str = "") -> int:
    rules = attendee_rules()
    price_count = max(1, math.ceil(total_price / int(rules.get("price_per_person") or 30000)))
    min_attendees = max(1, int(rules.get("min_attendees") or 2))
    max_attendees = max(min_attendees, int(rules.get("max_attendees") or 10))
    if max_attendee_store_exception(total_price, store_name, rules):
        return max_attendees
    food = food_count or 0
    drink = drink_count or 0
    if drink and not food:
        item_based = drink
    else:
        item_based = 1
    return min(max_attendees, max(min_attendees, price_count, item_based))
