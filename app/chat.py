import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
import httpx
from sqlalchemy import func
from sqlalchemy.orm import Session

from .models import Check, Mark, Person
from .services import list_months_with_checks, month_start_end, now_local

MODELSCOPE_BASE_URL = os.getenv("MODELSCOPE_BASE_URL", "https://api-inference.modelscope.cn/v1")
MODELSCOPE_API_KEY = os.getenv("MODELSCOPE_API_KEY", "")
MODELSCOPE_MODEL = os.getenv("MODELSCOPE_MODEL", "Qwen2.5-7B-Instruct")

MONTH_NAMES = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}


@dataclass
class ChatPlan:
    intent: str
    person_name: str | None = None
    month: str | None = None
    relative_month: str | None = None
    needs_clarification: bool = False
    clarifying_question: str | None = None
    source: str = "heuristic"


def answer_question(db: Session, question: str, now: datetime | None = None) -> dict:
    now = now or now_local()
    clean_question = question.strip()
    if not clean_question:
        return {
            "answer": "Not found in DB. Please enter a question.",
            "details": None,
            "clarifying_question": "Try asking about a month and a person name.",
        }

    plan = interpret_question(db, clean_question, now)

    if plan.intent == "person_month_summary":
        return handle_person_month_summary(db, clean_question, plan, now)
    if plan.intent == "absences_month_summary":
        return handle_absences_month_summary(db, clean_question, plan, now)

    return not_found_response(
        db,
        "Not found in DB. I could not determine what to query.",
        "Please ask about attendance or absences for a specific month.",
    )


def interpret_question(db: Session, question: str, now: datetime) -> ChatPlan:
    plan = call_modelscope_plan(question)
    if plan:
        plan.source = "modelscope"
    else:
        plan = heuristic_plan(question)

    plan.month = resolve_month_value(plan.month, plan.relative_month, question, now)

    if plan.intent == "unknown":
        plan.intent = heuristic_intent(question)

    if plan.intent == "person_month_summary":
        person = resolve_person_from_plan(db, plan, question)
        if person:
            plan.person_name = person.name
        return plan

    return plan


def handle_person_month_summary(db: Session, question: str, plan: ChatPlan, now: datetime) -> dict:
    person, person_candidates = resolve_person(
        db, plan.person_name or "", question
    )
    if not person:
        if person_candidates:
            names = ", ".join(person_candidates[:5])
            clarifying = f"Which person did you mean? Matches: {names}."
        else:
            clarifying = "Which person? Please provide a full name."
        return not_found_response(
            db,
            "Not found in DB. Person not found.",
            clarifying,
        )

    month = plan.month
    if not month:
        available = list_months_with_checks(db)
        if available:
            clarifying = f"Which month? Available months: {', '.join(available)}."
        else:
            clarifying = "Which month? There are no checks recorded yet."
        return not_found_response(
            db,
            "Not found in DB. Month not specified.",
            clarifying,
        )

    start, end = month_start_end(month)
    checks = (
        db.query(Check)
        .filter(Check.timestamp >= start, Check.timestamp < end)
        .order_by(Check.timestamp)
        .all()
    )
    if not checks:
        return not_found_response(
            db,
            f"Not found in DB. No checks in {month}.",
            "Try a different month with check records.",
        )

    check_ids = [check.id for check in checks]
    marks = (
        db.query(Mark)
        .filter(Mark.person_id == person.id, Mark.check_id.in_(check_ids))
        .all()
    )
    present = sum(1 for mark in marks if mark.status == "present")
    absent = sum(1 for mark in marks if mark.status == "absent")
    marked = present + absent
    unmarked = max(0, len(checks) - marked)
    absence_rate = (absent / len(checks) * 100) if checks else 0

    answer = (
        f"{person.name} attendance summary for {month}: "
        f"{present} present, {absent} absent, {unmarked} unmarked "
        f"across {len(checks)} checks."
    )

    details = [
        {"label": "Person", "value": f"{person.name} (id {person.id})"},
        {"label": "Month", "value": month},
        {
            "label": "Range",
            "value": f"{start.strftime('%Y-%m-%d')} to {end.strftime('%Y-%m-%d')}",
        },
        {"label": "Checks", "value": str(len(checks))},
        {"label": "Present", "value": str(present)},
        {"label": "Absent", "value": str(absent)},
        {"label": "Unmarked", "value": str(unmarked)},
        {"label": "Absence rate", "value": f"{absence_rate:.1f}%"},
        {
            "label": "Filters",
            "value": "Check timestamps within range; marks for person_id.",
        },
    ]

    return {
        "answer": answer,
        "details": details,
        "clarifying_question": None,
    }


def handle_absences_month_summary(db: Session, question: str, plan: ChatPlan, now: datetime) -> dict:
    month = plan.month
    if not month:
        available = list_months_with_checks(db)
        if available:
            clarifying = f"Which month? Available months: {', '.join(available)}."
        else:
            clarifying = "Which month? There are no checks recorded yet."
        return not_found_response(
            db,
            "Not found in DB. Month not specified.",
            clarifying,
        )

    start, end = month_start_end(month)
    checks_count = (
        db.query(func.count(Check.id))
        .filter(Check.timestamp >= start, Check.timestamp < end)
        .scalar()
        or 0
    )
    active_people = (
        db.query(func.count(Person.id))
        .filter(Person.active.is_(True))
        .scalar()
        or 0
    )
    absences = (
        db.query(func.count(Mark.id))
        .join(Person, Mark.person_id == Person.id)
        .join(Check, Mark.check_id == Check.id)
        .filter(
            Person.active.is_(True),
            Mark.status == "absent",
            Check.timestamp >= start,
            Check.timestamp < end,
        )
        .scalar()
        or 0
    )

    if checks_count == 0:
        return not_found_response(
            db,
            f"Not found in DB. No checks in {month}.",
            "Try a different month with check records.",
        )

    answer = (
        f"Absences in {month}: {absences} absent marks "
        f"across {checks_count} checks for {active_people} active people."
    )

    details = [
        {"label": "Month", "value": month},
        {
            "label": "Range",
            "value": f"{start.strftime('%Y-%m-%d')} to {end.strftime('%Y-%m-%d')}",
        },
        {"label": "Checks", "value": str(checks_count)},
        {"label": "Active people", "value": str(active_people)},
        {"label": "Absences", "value": str(absences)},
        {
            "label": "Filters",
            "value": "Status=absent, active people only, check timestamps within range.",
        },
    ]

    return {
        "answer": answer,
        "details": details,
        "clarifying_question": None,
    }


def not_found_response(db: Session, message: str, clarifying: str | None) -> dict:
    return {
        "answer": message,
        "details": None,
        "clarifying_question": clarifying,
    }


def resolve_person_from_plan(db: Session, plan: ChatPlan, question: str) -> Person | None:
    if plan.person_name:
        person, _ = resolve_person(db, plan.person_name, question)
        return person
    person, _ = resolve_person(db, "", question)
    return person


def resolve_person(db: Session, name: str, question: str) -> tuple[Person | None, list[str]]:
    candidates = []
    clean_name = normalize_name(name)
    if clean_name:
        matches = find_people_by_name(db, clean_name)
        if len(matches) == 1:
            return matches[0], []
        if len(matches) > 1:
            return None, [person.name for person in matches]

    people = db.query(Person).order_by(Person.name).all()
    q_lower = question.lower()
    matched = []
    for person in people:
        pattern = r"\b" + re.escape(person.name.lower()) + r"\b"
        if re.search(pattern, q_lower):
            matched.append(person)
    if len(matched) == 1:
        return matched[0], []
    if len(matched) > 1:
        return None, [person.name for person in matched]
    return None, []


def find_people_by_name(db: Session, name: str) -> list[Person]:
    lowered = name.lower()
    exact = (
        db.query(Person)
        .filter(func.lower(Person.name) == lowered)
        .order_by(Person.name)
        .all()
    )
    if exact:
        return exact
    return (
        db.query(Person)
        .filter(func.lower(Person.name).like(f"%{lowered}%"))
        .order_by(Person.name)
        .all()
    )


def normalize_name(name: str) -> str:
    cleaned = name.strip()
    if cleaned.endswith("'s") or cleaned.endswith("’s"):
        cleaned = cleaned[:-2]
    return cleaned.strip()


def resolve_month_value(
    month_value: str | None, relative_value: str | None, question: str, now: datetime
) -> str | None:
    if month_value:
        parsed = parse_month_text(month_value, now)
        if parsed:
            return parsed
    if relative_value:
        parsed = parse_relative_month(relative_value, now)
        if parsed:
            return parsed
    parsed = parse_month_text(question, now)
    if parsed:
        return parsed
    return parse_relative_month(question, now)


def parse_relative_month(text: str, now: datetime) -> str | None:
    lowered = text.lower()
    if lowered in ("last", "previous"):
        year = now.year
        month = now.month - 1
        if month < 1:
            month = 12
            year -= 1
        return f"{year:04d}-{month:02d}"
    if lowered in ("this", "current"):
        return f"{now.year:04d}-{now.month:02d}"
    if "last month" in lowered or "previous month" in lowered:
        year = now.year
        month = now.month - 1
        if month < 1:
            month = 12
            year -= 1
        return f"{year:04d}-{month:02d}"
    if "this month" in lowered or "current month" in lowered:
        return f"{now.year:04d}-{now.month:02d}"
    return None


def parse_month_text(text: str, now: datetime) -> str | None:
    match = re.search(r"\b(20\d{2})-(0?[1-9]|1[0-2])\b", text)
    if match:
        year = int(match.group(1))
        month = int(match.group(2))
        return f"{year:04d}-{month:02d}"

    match = re.search(r"\b([A-Za-z]{3,9})\s+(20\d{2})\b", text)
    if match:
        month_name = match.group(1).lower()
        year = int(match.group(2))
        month = MONTH_NAMES.get(month_name)
        if month:
            return f"{year:04d}-{month:02d}"

    match = re.search(r"\b(20\d{2})\s+([A-Za-z]{3,9})\b", text)
    if match:
        year = int(match.group(1))
        month_name = match.group(2).lower()
        month = MONTH_NAMES.get(month_name)
        if month:
            return f"{year:04d}-{month:02d}"

    return None


def call_modelscope_plan(question: str) -> ChatPlan | None:
    if not MODELSCOPE_API_KEY:
        return None

    system_prompt = (
        "You are a query planner for an attendance database. "
        "Return JSON only with keys: intent, person_name, month, relative_month, "
        "needs_clarification, clarifying_question. "
        "Allowed intents: person_month_summary, absences_month_summary, unknown. "
        "month must be YYYY-MM when explicit. relative_month can be 'last' or 'this'."
    )

    payload = {
        "model": MODELSCOPE_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": question},
        ],
        "temperature": 0,
    }
    headers = {
        "Authorization": f"Bearer {MODELSCOPE_API_KEY}",
        "Content-Type": "application/json",
    }
    url = f"{MODELSCOPE_BASE_URL.rstrip('/')}/chat/completions"

    try:
        with httpx.Client(timeout=15) as client:
            response = client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
    except Exception:
        return None

    content = (
        data.get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
    )
    parsed = extract_json(content)
    if not parsed:
        return None

    intent = parsed.get("intent", "unknown")
    return ChatPlan(
        intent=intent,
        person_name=parsed.get("person_name"),
        month=parsed.get("month"),
        relative_month=parsed.get("relative_month"),
        needs_clarification=bool(parsed.get("needs_clarification", False)),
        clarifying_question=parsed.get("clarifying_question"),
        source="modelscope",
    )


def extract_json(content: str) -> dict | None:
    match = re.search(r"\{.*\}", content, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def heuristic_plan(question: str) -> ChatPlan:
    intent = heuristic_intent(question)
    return ChatPlan(intent=intent, source="heuristic")


def heuristic_intent(question: str) -> str:
    lowered = question.lower()
    if "attendance" in lowered and ("summary" in lowered or "record" in lowered):
        return "person_month_summary"
    if "how many" in lowered and ("absence" in lowered or "absent" in lowered):
        return "absences_month_summary"
    if "absence" in lowered or "absent" in lowered:
        return "absences_month_summary"
    return "unknown"
