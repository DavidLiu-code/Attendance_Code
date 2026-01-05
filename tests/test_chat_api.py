import json
from datetime import datetime

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.chat_client import LLMResponse, MODEL_CATALOG
from app.chat_service import handle_chat_request
from app.db import Base
from app.models import ChatSummary, Check, Mark, Person


class FakeLLMClient:
    def __init__(self, responses):
        self.responses = list(responses)

    def chat_completions(self, **kwargs):
        if not self.responses:
            raise AssertionError("No more fake LLM responses.")
        return LLMResponse(content=self.responses.pop(0))


def make_db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def seed_data(db):
    alice = Person(name="Alice", current_salary=100, active=True)
    bob = Person(name="Bob", current_salary=150, active=True)
    db.add_all([alice, bob])
    db.commit()

    check = Check(timestamp=datetime(2025, 12, 5, 10, 0), created_by="Liu")
    db.add(check)
    db.commit()

    db.add_all(
        [
            Mark(check_id=check.id, person_id=alice.id, status="present"),
            Mark(check_id=check.id, person_id=bob.id, status="absent"),
        ]
    )
    db.commit()
    return alice, bob, check


def plan_with_query(query, params=None):
    return json.dumps(
        {
            "intent": "attendance_query",
            "needs_db": True,
            "sql_requests": [
                {"query": query, "params": params or {}, "purpose": "test"},
            ],
            "final_answer_ready": False,
            "clarifying_question": "",
        }
    )


def final_answer_json(answer="OK"):
    return json.dumps(
        {"answer": answer, "data_preview": [], "assumptions": [], "confidence": "high"}
    )


def test_user_scope_filters_rows():
    db = make_db()
    alice, bob, _ = seed_data(db)

    query = "select person_id, status from marks order by person_id"
    fake = FakeLLMClient([plan_with_query(query), final_answer_json()])
    result = handle_chat_request(
        db,
        {"user_id": "Alice", "role": "user", "message": "Show attendance."},
        llm_client=fake,
    )
    preview_ids = {row["person_id"] for row in result["data_preview"]}
    assert preview_ids == {alice.id}
    assert bob.id not in preview_ids


def test_manager_scope_without_mapping_returns_empty():
    db = make_db()
    seed_data(db)

    query = "select person_id, status from marks"
    fake = FakeLLMClient([plan_with_query(query), final_answer_json("Should not use")])
    result = handle_chat_request(
        db,
        {"user_id": "manager1", "role": "manager", "message": "Show attendance."},
        llm_client=fake,
    )
    assert "Not found in database" in result["answer"]
    assert result["data_preview"] == []


def test_salary_redaction_for_user():
    db = make_db()
    alice, _, _ = seed_data(db)

    query = "select person_id, salary_before, salary_after from salary_history"
    db.execute(
        text(
            "insert into salary_history (person_id, month, salary_before, delta, salary_after, reason, computed_at) "
            "values (:pid, '2025-12', 100, 50, 150, 'perfect', '2025-12-31 00:00')"
        ),
        {"pid": alice.id},
    )
    db.commit()

    fake = FakeLLMClient([plan_with_query(query), final_answer_json()])
    result = handle_chat_request(
        db,
        {"user_id": "Alice", "role": "user", "message": "Show salary."},
        llm_client=fake,
    )
    preview = result["data_preview"][0]
    assert preview["salary_before"] == "[redacted]"
    assert preview["salary_after"] == "[redacted]"


def test_salary_visible_for_admin():
    db = make_db()
    alice, _, _ = seed_data(db)
    db.execute(
        text(
            "insert into salary_history (person_id, month, salary_before, delta, salary_after, reason, computed_at) "
            "values (:pid, '2025-12', 100, 50, 150, 'perfect', '2025-12-31 00:00')"
        ),
        {"pid": alice.id},
    )
    db.commit()

    query = "select person_id, salary_before, salary_after from salary_history"
    fake = FakeLLMClient([plan_with_query(query), final_answer_json()])
    result = handle_chat_request(
        db,
        {"user_id": "admin", "role": "admin", "message": "Show salary."},
        llm_client=fake,
    )
    preview = result["data_preview"][0]
    assert preview["salary_before"] == 100
    assert preview["salary_after"] == 150


def test_model_key_override():
    db = make_db()
    seed_data(db)

    fake = FakeLLMClient([plan_with_query("select 1 as one"), final_answer_json()])
    result = handle_chat_request(
        db,
        {
            "user_id": "admin",
            "role": "admin",
            "model_key": "fast",
            "message": "Quick check.",
        },
        llm_client=fake,
    )
    assert result["model_used"] == MODEL_CATALOG["fast"]


def test_auto_long_model():
    db = make_db()
    seed_data(db)

    fake = FakeLLMClient([plan_with_query("select 1 as one"), final_answer_json()])
    result = handle_chat_request(
        db,
        {
            "user_id": "admin",
            "role": "admin",
            "message": "Need a detailed analysis of attendance.",
        },
        llm_client=fake,
    )
    assert result["model_used"] == MODEL_CATALOG["long"]


def test_sql_injection_rejected():
    db = make_db()
    seed_data(db)

    bad_query = "select * from marks; drop table people"
    fake = FakeLLMClient([plan_with_query(bad_query), final_answer_json("Ignore")])
    result = handle_chat_request(
        db,
        {"user_id": "admin", "role": "admin", "message": "Show attendance."},
        llm_client=fake,
    )
    assert "Not found in database" in result["answer"]
    assert result["data_preview"] == []


def test_empty_results_prompt_clarification():
    db = make_db()
    seed_data(db)

    query = "select person_id, status from marks where check_id = 9999"
    fake = FakeLLMClient([plan_with_query(query), final_answer_json("No rows")])
    result = handle_chat_request(
        db,
        {"user_id": "admin", "role": "admin", "message": "Show attendance."},
        llm_client=fake,
    )
    assert "Not found in database" in result["answer"]


def test_summary_update(monkeypatch):
    db = make_db()
    seed_data(db)

    monkeypatch.setattr("app.chat_service.CHAT_SUMMARY_THRESHOLD_MESSAGES", 2)
    monkeypatch.setattr("app.chat_service.CHAT_RECENT_LIMIT", 1)
    fake = FakeLLMClient(
        [
            plan_with_query("select 1 as one"),
            final_answer_json(),
            "summary text",
        ]
    )
    result = handle_chat_request(
        db,
        {"user_id": "admin", "role": "admin", "message": "Show attendance."},
        llm_client=fake,
    )
    summary = db.query(ChatSummary).filter(ChatSummary.session_id == result["session_id"]).first()
    assert summary is not None
    assert "summary" in summary.summary_text


def test_debug_sql_used_toggle(monkeypatch):
    db = make_db()
    seed_data(db)

    monkeypatch.setattr("app.chat_service.DEBUG_CHAT", False)
    fake_off = FakeLLMClient([plan_with_query("select 1 as one"), final_answer_json()])
    result_off = handle_chat_request(
        db,
        {"user_id": "admin", "role": "admin", "message": "Check."},
        llm_client=fake_off,
    )
    assert "sql_used" not in result_off["debug"]

    monkeypatch.setattr("app.chat_service.DEBUG_CHAT", True)
    fake_on = FakeLLMClient([plan_with_query("select 1 as one"), final_answer_json()])
    result_on = handle_chat_request(
        db,
        {"user_id": "admin", "role": "admin", "message": "Check again."},
        llm_client=fake_on,
    )
    assert "sql_used" in result_on["debug"]
    assert result_on["debug"]["sql_used"]
