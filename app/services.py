from datetime import datetime
from typing import Dict, Iterable, List, Tuple
import os
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from .models import Check, Mark, Person, SalaryHistory

START_SALARY = 50
SALARY_STEP = 50
SALARY_CAP = 600
DEFAULT_TZ = "Asia/Shanghai"


def get_timezone() -> ZoneInfo:
    name = os.getenv("APP_TIMEZONE", DEFAULT_TZ)
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo(DEFAULT_TZ)


def now_local() -> datetime:
    return datetime.now(tz=get_timezone()).replace(tzinfo=None)


def parse_month(month_str: str) -> Tuple[int, int]:
    parts = month_str.split("-")
    if len(parts) != 2:
        raise ValueError("Invalid month format. Use YYYY-MM.")
    try:
        year = int(parts[0])
        month = int(parts[1])
    except ValueError as exc:
        raise ValueError("Invalid month format. Use YYYY-MM.") from exc
    if month < 1 or month > 12:
        raise ValueError("Invalid month format. Use YYYY-MM.")
    return year, month


def month_start_end(month_str: str) -> Tuple[datetime, datetime]:
    year, month = parse_month(month_str)
    start = datetime(year, month, 1)
    if month == 12:
        end = datetime(year + 1, 1, 1)
    else:
        end = datetime(year, month + 1, 1)
    return start, end


def get_month_str(dt: datetime) -> str:
    return f"{dt.year:04d}-{dt.month:02d}"


def get_checks_for_month(db: Session, month_str: str) -> List[Check]:
    start, end = month_start_end(month_str)
    return (
        db.query(Check)
        .filter(Check.timestamp >= start, Check.timestamp < end)
        .order_by(Check.timestamp)
        .all()
    )


def collect_marks_for_checks(db: Session, check_ids: Iterable[int]) -> Dict[int, Dict[int, str]]:
    ids = list(check_ids)
    if not ids:
        return {}
    marks = db.query(Mark).filter(Mark.check_id.in_(ids)).all()
    by_check: Dict[int, Dict[int, str]] = {}
    for mark in marks:
        by_check.setdefault(mark.check_id, {})[mark.person_id] = mark.status
    return by_check


def is_perfect_month(
    checks: Iterable[Check],
    marks_by_check: Dict[int, Dict[int, str]],
    person_id: int,
) -> bool:
    checks_list = list(checks)
    if not checks_list:
        return False
    for check in checks_list:
        status = marks_by_check.get(check.id, {}).get(person_id)
        if status != "present":
            return False
    return True


def apply_month_change(current_salary: int, perfect: bool) -> Tuple[int, int, str]:
    if perfect:
        new_salary = min(SALARY_CAP, current_salary + SALARY_STEP)
    else:
        new_salary = current_salary - SALARY_STEP
    delta = new_salary - current_salary
    reason = "perfect" if perfect else "not_perfect"
    return delta, new_salary, reason


def preview_month(db: Session, month_str: str):
    checks = get_checks_for_month(db, month_str)
    marks_by_check = collect_marks_for_checks(db, [check.id for check in checks])
    people = db.query(Person).order_by(Person.name).all()
    rows = []
    for person in people:
        perfect = is_perfect_month(checks, marks_by_check, person.id)
        delta, after, reason = apply_month_change(person.current_salary, perfect)
        rows.append(
            {
                "person": person,
                "perfect": perfect,
                "delta": delta,
                "after": after,
                "reason": reason,
            }
        )
    return {
        "rows": rows,
        "checks_count": len(checks),
        "has_checks": len(checks) > 0,
    }


def close_month(db: Session, month_str: str) -> int:
    if db.query(SalaryHistory).filter(SalaryHistory.month == month_str).first():
        raise ValueError("Month already closed.")

    checks = get_checks_for_month(db, month_str)
    if not checks:
        raise ValueError("No checks found for this month.")

    marks_by_check = collect_marks_for_checks(db, [check.id for check in checks])
    people = db.query(Person).order_by(Person.name).all()
    computed_at = now_local()

    for person in people:
        salary_before = person.current_salary
        perfect = is_perfect_month(checks, marks_by_check, person.id)
        delta, salary_after, reason = apply_month_change(salary_before, perfect)
        history = SalaryHistory(
            person_id=person.id,
            month=month_str,
            salary_before=salary_before,
            delta=delta,
            salary_after=salary_after,
            reason=reason,
            computed_at=computed_at,
        )
        person.current_salary = salary_after
        db.add(history)

    db.commit()
    return len(people)


def list_months_with_checks(db: Session) -> List[str]:
    checks = db.query(Check).all()
    months = sorted({get_month_str(check.timestamp) for check in checks})
    return months


def recalculate_all(db: Session) -> int:
    db.query(SalaryHistory).delete(synchronize_session=False)
    people = db.query(Person).order_by(Person.name).all()
    for person in people:
        person.current_salary = START_SALARY

    months = list_months_with_checks(db)
    for month in months:
        checks = get_checks_for_month(db, month)
        marks_by_check = collect_marks_for_checks(db, [check.id for check in checks])
        computed_at = now_local()
        for person in people:
            salary_before = person.current_salary
            perfect = is_perfect_month(checks, marks_by_check, person.id)
            delta, salary_after, reason = apply_month_change(salary_before, perfect)
            history = SalaryHistory(
                person_id=person.id,
                month=month,
                salary_before=salary_before,
                delta=delta,
                salary_after=salary_after,
                reason=reason,
                computed_at=computed_at,
            )
            person.current_salary = salary_after
            db.add(history)

    db.commit()
    return len(months)
