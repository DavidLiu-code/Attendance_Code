from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Check, Mark, Person, SalaryHistory
from app.services import (
    SALARY_CAP,
    SALARY_STEP,
    START_SALARY,
    close_month,
    collect_marks_for_checks,
    get_checks_for_month,
    is_perfect_month,
    recalculate_all,
)


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


def test_perfect_month_detection(db):
    person = Person(name="Alice", current_salary=START_SALARY)
    db.add(person)
    db.commit()

    check_one = Check(timestamp=datetime(2024, 1, 5, 9, 0))
    check_two = Check(timestamp=datetime(2024, 1, 20, 9, 0))
    db.add_all([check_one, check_two])
    db.commit()

    db.add_all(
        [
            Mark(check_id=check_one.id, person_id=person.id, status="present"),
            Mark(check_id=check_two.id, person_id=person.id, status="present"),
        ]
    )
    db.commit()

    checks = get_checks_for_month(db, "2024-01")
    marks = collect_marks_for_checks(db, [check.id for check in checks])
    assert is_perfect_month(checks, marks, person.id) is True


def test_missing_mark_not_perfect(db):
    person = Person(name="Ben", current_salary=START_SALARY)
    db.add(person)
    db.commit()

    check_one = Check(timestamp=datetime(2024, 2, 5, 9, 0))
    check_two = Check(timestamp=datetime(2024, 2, 10, 9, 0))
    db.add_all([check_one, check_two])
    db.commit()

    db.add(Mark(check_id=check_one.id, person_id=person.id, status="present"))
    db.commit()

    checks = get_checks_for_month(db, "2024-02")
    marks = collect_marks_for_checks(db, [check.id for check in checks])
    assert is_perfect_month(checks, marks, person.id) is False


def test_salary_cap_at_600(db):
    person = Person(name="Cap", current_salary=START_SALARY)
    db.add(person)
    db.commit()

    checks = []
    for month in range(1, 13):
        checks.append(Check(timestamp=datetime(2024, month, 1, 9, 0)))
    db.add_all(checks)
    db.commit()

    db.add_all(
        [
            Mark(check_id=check.id, person_id=person.id, status="present")
            for check in checks
        ]
    )
    db.commit()

    close_month(db, "2024-12")
    db.refresh(person)

    history = (
        db.query(SalaryHistory)
        .filter(SalaryHistory.person_id == person.id, SalaryHistory.month == "2024-12")
        .one()
    )
    assert history.salary_before == SALARY_CAP
    assert history.delta == 0
    assert history.salary_after == SALARY_CAP
    assert person.current_salary == SALARY_CAP


def test_multi_month_recalc_correctness(db):
    person = Person(name="Dana", current_salary=START_SALARY)
    db.add(person)
    db.commit()

    jan = Check(timestamp=datetime(2024, 1, 2, 9, 0))
    feb = Check(timestamp=datetime(2024, 2, 2, 9, 0))
    db.add_all([jan, feb])
    db.commit()

    db.add_all(
        [
            Mark(check_id=jan.id, person_id=person.id, status="present"),
            Mark(check_id=feb.id, person_id=person.id, status="absent"),
        ]
    )
    db.commit()

    months = recalculate_all(db)
    assert months == 2

    history = db.query(SalaryHistory).order_by(SalaryHistory.month).all()
    assert [row.month for row in history] == ["2024-01", "2024-02"]
    assert history[0].salary_before == START_SALARY
    assert history[0].salary_after == START_SALARY + SALARY_STEP
    assert history[1].salary_before == START_SALARY + SALARY_STEP
    assert history[1].salary_after == 0
    db.refresh(person)
    assert person.current_salary == 0
