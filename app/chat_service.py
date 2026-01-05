import json
import os
import re
import uuid
from typing import Any

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from .chat_client import DEFAULT_MODEL_KEY, MODEL_CATALOG, get_llm_client, get_model_name
from .chat_retrieval import retrieve_memory, store_embedding
from .models import ChatMessage, ChatSession, ChatSummary, ChatUserScope, Person
from .services import now_local

CHAT_RECENT_LIMIT = int(os.getenv("CHAT_RECENT_LIMIT", "16"))
CHAT_SUMMARY_THRESHOLD_MESSAGES = int(os.getenv("CHAT_SUMMARY_THRESHOLD_MESSAGES", "40"))
CHAT_SUMMARY_THRESHOLD_TOKENS = int(os.getenv("CHAT_SUMMARY_THRESHOLD_TOKENS", "6000"))
DEBUG_CHAT = os.getenv("DEBUG_CHAT", "false").lower() == "true"
ALLOWED_TABLES = {"people", "checks", "marks", "salary_history"}

SYSTEM_PROMPT = """You are an assistant inside an Attendance + Salary system. You must be accurate.
Rules:

If a user asks about attendance/salary facts, you MUST use the provided tools to query the database.

You are NOT allowed to guess, estimate, or invent. If data is missing, say "Not found in database" and ask a clarifying question.

You must respect access control: regular users see only themselves, managers see their teams, admins see all; salary fields require explicit permission.

Prefer returning results as concise bullet points plus a small table preview.

Output MUST be valid JSON with keys: answer, data_preview, assumptions, confidence. Do not output extra text outside JSON."""


def handle_chat_request(db: Session, payload: dict, llm_client=None) -> dict:
    user_id = (payload.get("user_id") or "").strip()
    role = (payload.get("role") or "").strip()
    message = (payload.get("message") or "").strip()
    session_id = (payload.get("session_id") or "").strip() or None
    model_key = (payload.get("model_key") or "").strip() or None
    stream = bool(payload.get("stream", False))

    if stream:
        return {"error": "Streaming is not supported yet."}
    if not user_id:
        return {"error": "user_id is required."}
    if role not in ("user", "manager", "admin"):
        return {"error": "role must be user, manager, or admin."}
    if not message:
        return {"error": "message is required."}

    session = get_or_create_session(db, session_id, user_id, role)
    requested_model_key = model_key
    model_key = resolve_model_key(model_key, session.default_model_key, message)
    model_name = get_model_name(model_key)
    if requested_model_key and session.default_model_key != requested_model_key:
        session.default_model_key = requested_model_key
        session.updated_at = now_local()
        db.commit()

    scope = get_user_scope(db, user_id, role)

    user_meta = {"model_key": model_key}
    user_message = store_session_message(db, session.session_id, "user", message, user_meta)
    store_embedding(db, user_message.id, message)

    summary_text = get_session_summary(db, session.session_id)
    recent_messages = get_session_messages(db, session.session_id, CHAT_RECENT_LIMIT)
    memory = retrieve_memory(db, session.session_id, message)
    schema = lookup_schema()

    llm_client = llm_client or get_llm_client()

    plan = build_plan(
        llm_client,
        model_name,
        message,
        scope,
        schema,
        summary_text,
        recent_messages,
        memory,
    )

    needs_db = plan.get("needs_db", False) or question_needs_db(message)
    sql_requests = plan.get("sql_requests") or []
    clarifying_question = plan.get("clarifying_question")

    sql_used = []
    sql_results = []
    if needs_db:
        if not sql_requests:
            answer = build_clarifying_answer(clarifying_question, db)
            response = build_response(
                session.session_id,
                model_name,
                answer,
                [],
                [],
                "low",
                [],
                recent_messages,
                summary_text,
            )
            store_assistant_response(db, session.session_id, response["answer"], response)
            update_session_summary_if_needed(db, session.session_id, llm_client)
            return response
        for request in sql_requests:
            query = request.get("query", "")
            params = request.get("params") or {}
            purpose = request.get("purpose", "")
            query_lower = query.lower()
            if scope.get("allowed_person_ids") is not None and "person_id" not in query_lower and "people.id" not in query_lower:
                rows = []
            else:
                rows = run_sql(db, query, params)
            rows = apply_scope_to_rows(rows, scope)
            sql_results.append(
                {
                    "purpose": purpose,
                    "query": query,
                    "params": params,
                    "rows": rows,
                }
            )
            sql_used.append({"query": query, "params": params})

    preview = build_data_preview(sql_results, scope)

    final_output = build_final_answer(
        llm_client,
        model_name,
        message,
        scope,
        schema,
        summary_text,
        recent_messages,
        memory,
        sql_results,
        preview,
    )

    answer = final_output.get("answer") or "Not found in database."
    assumptions = final_output.get("assumptions") or []
    if not isinstance(assumptions, list):
        assumptions = [str(assumptions)]
    confidence = final_output.get("confidence") or "low"
    if confidence not in ("high", "medium", "low"):
        confidence = "low"
    if needs_db and not preview:
        answer = build_clarifying_answer(clarifying_question, db)
        assumptions = []
        confidence = "low"

    response = build_response(
        session.session_id,
        model_name,
        answer,
        preview,
        assumptions,
        confidence,
        sql_used if DEBUG_CHAT else [],
        recent_messages,
        summary_text,
    )

    store_assistant_response(db, session.session_id, response["answer"], response)
    message_id = response_meta_id(db, session.session_id)
    if message_id:
        store_embedding(db, message_id, response["answer"])
    update_session_summary_if_needed(db, session.session_id, llm_client)
    return response


def store_assistant_response(db: Session, session_id: str, answer: str, meta: dict) -> None:
    store_session_message(db, session_id, "assistant", answer, meta)


def response_meta_id(db: Session, session_id: str) -> int:
    last = (
        db.query(ChatMessage)
        .filter(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.id.desc())
        .first()
    )
    return last.id if last else 0


def resolve_model_key(requested: str | None, session_default: str, message: str) -> str:
    if requested:
        return requested
    lowered = message.lower()
    if "long report" in lowered or "detailed analysis" in lowered or "full report" in lowered:
        if "long" in MODEL_CATALOG:
            return "long"
    return session_default or DEFAULT_MODEL_KEY


def question_needs_db(message: str) -> bool:
    lowered = message.lower()
    keywords = ("attendance", "absent", "absence", "salary", "pay", "check")
    return any(keyword in lowered for keyword in keywords)


def build_plan(
    llm_client,
    model_name: str,
    message: str,
    scope: dict,
    schema: dict,
    summary_text: str | None,
    recent_messages: list[dict],
    memory: list[dict],
) -> dict:
    prompt = {
        "role": "system",
        "content": (
            "You are a planning assistant. Return JSON only with keys: intent, "
            "needs_db, sql_requests, final_answer_ready, clarifying_question. "
            "intent must be one of attendance_query, salary_query, policy, chitchat, other. "
            "sql_requests is a list of {query, params, purpose}. Use parameterized SQL."
        ),
    }
    context = build_context_payload(scope, schema, summary_text, recent_messages, memory)
    messages = [
        prompt,
        {"role": "user", "content": json.dumps(context)},
        {"role": "user", "content": f"User question: {message}"},
    ]
    try:
        response = llm_client.chat_completions(
            model_name=model_name,
            messages=messages,
            temperature=0.0,
            max_tokens=800,
            stream=False,
        )
        return parse_json(response.content) or {}
    except Exception:
        return {}


def build_final_answer(
    llm_client,
    model_name: str,
    message: str,
    scope: dict,
    schema: dict,
    summary_text: str | None,
    recent_messages: list[dict],
    memory: list[dict],
    sql_results: list[dict],
    preview: list[dict],
) -> dict:
    context = build_context_payload(scope, schema, summary_text, recent_messages, memory)
    context["sql_results"] = sql_results
    context["data_preview"] = preview
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(context)},
        {
            "role": "user",
            "content": (
                f"User question: {message}\n"
                "Include explicit date ranges and filters in the answer."
            ),
        },
    ]
    try:
        response = llm_client.chat_completions(
            model_name=model_name,
            messages=messages,
            temperature=0.0,
            max_tokens=800,
            stream=False,
        )
        return parse_json(response.content) or {}
    except Exception:
        return {}


def build_context_payload(
    scope: dict,
    schema: dict,
    summary_text: str | None,
    recent_messages: list[dict],
    memory: list[dict],
) -> dict:
    return {
        "tools": [
            "lookup_schema",
            "run_sql",
            "get_user_scope",
            "store_session_message",
            "get_session_messages",
            "get_session_summary",
            "update_session_summary",
        ],
        "scope": scope,
        "schema": schema,
        "summary": summary_text or "",
        "recent_messages": recent_messages,
        "memory": memory,
        "current_time": now_local().strftime("%Y-%m-%d %H:%M"),
    }


def build_response(
    session_id: str,
    model_name: str,
    answer: str,
    data_preview: list[dict],
    assumptions: list,
    confidence: str,
    sql_used: list[dict],
    recent_messages: list[dict],
    summary_text: str | None,
) -> dict:
    tokens_estimate = {
        "history_tokens": sum(estimate_tokens(item.get("content", "")) for item in recent_messages),
        "summary_tokens": estimate_tokens(summary_text or ""),
        "prompt_tokens": estimate_tokens(answer),
    }
    debug = {"tokens_estimate": tokens_estimate}
    if DEBUG_CHAT:
        debug["sql_used"] = sql_used
    return {
        "session_id": session_id,
        "model_used": model_name,
        "answer": answer,
        "data_preview": data_preview[:20],
        "assumptions": assumptions,
        "confidence": confidence,
        "debug": debug,
    }


def build_clarifying_answer(clarifying: str | None, db: Session | None = None) -> str:
    if clarifying:
        return f"Not found in database. {clarifying}"
    if db:
        months = fetch_available_months(db)
        if months:
            return (
                "Not found in database. Please clarify the month or person. "
                f"Available months: {', '.join(months)}."
            )
    return "Not found in database. Please clarify your request."


def parse_json(content: str) -> dict | None:
    match = re.search(r"\{.*\}", content, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def lookup_schema() -> dict:
    return {
        "people": ["id", "name", "active", "current_salary"],
        "checks": ["id", "timestamp", "created_by"],
        "marks": ["id", "check_id", "person_id", "status"],
        "salary_history": [
            "person_id",
            "month",
            "salary_before",
            "delta",
            "salary_after",
            "reason",
            "computed_at",
        ],
    }


def run_sql(db: Session, query: str, params: dict[str, Any]) -> list[dict]:
    safe_query = sanitize_sql(query)
    if not safe_query:
        return []
    if params:
        for key in params:
            if f":{key}" not in safe_query:
                return []
    else:
        if re.search(r"'[^']*'", safe_query):
            return []
    result = db.execute(text(safe_query), params)
    rows = result.mappings().all()
    if len(rows) > 200:
        rows = rows[:200]
    return [dict(row) for row in rows]


def sanitize_sql(query: str) -> str | None:
    if not query:
        return None
    lowered = query.strip().lower()
    if not lowered.startswith("select"):
        return None
    if ";" in lowered:
        return None
    forbidden = ("insert", "update", "delete", "drop", "alter", "pragma")
    if any(keyword in lowered for keyword in forbidden):
        return None
    tables = extract_tables(lowered)
    if tables and any(table not in ALLOWED_TABLES for table in tables):
        return None
    return query


def get_user_scope(db: Session, user_id: str, role: str) -> dict:
    salary_visible = role == "admin"
    if role == "admin":
        return {
            "role": role,
            "allowed_person_ids": None,
            "salary_visible": salary_visible,
        }
    if role == "manager":
        scoped = (
            db.query(ChatUserScope)
            .filter(ChatUserScope.user_id == user_id)
            .all()
        )
        allowed = [row.person_id for row in scoped]
        salary_visible = any(row.can_view_salary for row in scoped)
        return {
            "role": role,
            "allowed_person_ids": allowed,
            "salary_visible": salary_visible,
        }
    person = resolve_person_from_user_id(db, user_id)
    allowed_ids = [person.id] if person else []
    return {
        "role": role,
        "allowed_person_ids": allowed_ids,
        "salary_visible": False,
    }


def resolve_person_from_user_id(db: Session, user_id: str) -> Person | None:
    if user_id.isdigit():
        person = db.query(Person).filter(Person.id == int(user_id)).first()
        if person:
            return person
    return (
        db.query(Person)
        .filter(func.lower(Person.name) == user_id.lower())
        .first()
    )


def store_session_message(db: Session, session_id: str, role: str, content: str, meta: dict | None) -> ChatMessage:
    token_estimate = estimate_tokens(content)
    session = db.query(ChatSession).filter(ChatSession.session_id == session_id).first()
    if session:
        session.updated_at = now_local()
    message = ChatMessage(
        session_id=session_id,
        role=role,
        content=content,
        token_estimate=token_estimate,
        meta_json=json.dumps(meta or {}),
        created_at=now_local(),
    )
    db.add(message)
    db.commit()
    return message


def get_session_messages(db: Session, session_id: str, limit: int, offset: int = 0) -> list[dict]:
    messages = (
        db.query(ChatMessage)
        .filter(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    messages.reverse()
    return [
        {
            "role": msg.role,
            "content": msg.content,
            "created_at": msg.created_at.isoformat(sep=" "),
        }
        for msg in messages
    ]


def get_session_summary(db: Session, session_id: str) -> str | None:
    summary = db.query(ChatSummary).filter(ChatSummary.session_id == session_id).first()
    return summary.summary_text if summary else None


def update_session_summary(db: Session, session_id: str, summary_text: str) -> None:
    summary = db.query(ChatSummary).filter(ChatSummary.session_id == session_id).first()
    now = now_local()
    if summary:
        summary.summary_text = summary_text
        summary.updated_at = now
        summary.version += 1
    else:
        summary = ChatSummary(
            session_id=session_id,
            summary_text=summary_text,
            updated_at=now,
            version=1,
        )
        db.add(summary)
    db.commit()


def update_session_summary_if_needed(db: Session, session_id: str, llm_client=None) -> None:
    count = (
        db.query(ChatMessage)
        .filter(ChatMessage.session_id == session_id)
        .count()
    )
    total_tokens = (
        db.query(func.sum(ChatMessage.token_estimate))
        .filter(ChatMessage.session_id == session_id)
        .scalar()
        or 0
    )
    if count < CHAT_SUMMARY_THRESHOLD_MESSAGES and total_tokens < CHAT_SUMMARY_THRESHOLD_TOKENS:
        return

    older_messages = (
        db.query(ChatMessage)
        .filter(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at.desc())
        .offset(CHAT_RECENT_LIMIT)
        .all()
    )
    older_messages.reverse()
    if not older_messages:
        return

    summary_text = get_session_summary(db, session_id) or ""
    llm_client = llm_client or get_llm_client()
    summarizer_name = MODEL_CATALOG.get("summarizer") or get_model_name(DEFAULT_MODEL_KEY)

    summary_prompt = (
        "Summarize the following chat history into a concise memory. "
        "Capture user preferences, entities (employee IDs, teams), date ranges, "
        "open questions, and constraints. Return plain text only."
    )
    history_blob = "\n".join(
        f"{msg.role}: {msg.content}" for msg in older_messages
    )
    messages = [
        {"role": "system", "content": summary_prompt},
        {"role": "user", "content": summary_text},
        {"role": "user", "content": history_blob},
    ]
    try:
        response = llm_client.chat_completions(
            model_name=summarizer_name,
            messages=messages,
            temperature=0.2,
            max_tokens=600,
            stream=False,
        )
        new_summary = response.content.strip()
        if new_summary:
            update_session_summary(db, session_id, new_summary)
    except Exception:
        return


def build_data_preview(sql_results: list[dict], scope: dict) -> list[dict]:
    if not sql_results:
        return []
    preview = []
    for result in sql_results:
        for row in result.get("rows", []):
            preview.append(redact_row(row, scope))
            if len(preview) >= 20:
                return preview
    return preview


def redact_row(row: dict, scope: dict) -> dict:
    salary_visible = scope.get("salary_visible", False)
    if salary_visible:
        return row
    redacted = {}
    for key, value in row.items():
        if "salary" in key:
            redacted[key] = "[redacted]"
        else:
            redacted[key] = value
    return redacted


def get_or_create_session(db: Session, session_id: str | None, user_id: str, role: str) -> ChatSession:
    now = now_local()
    if session_id:
        session = (
            db.query(ChatSession)
            .filter(ChatSession.session_id == session_id)
            .first()
        )
        if session:
            session.updated_at = now
            db.commit()
            return session

    new_session_id = session_id or str(uuid.uuid4())
    session = ChatSession(
        session_id=new_session_id,
        user_id=user_id,
        role=role,
        default_model_key=DEFAULT_MODEL_KEY,
        created_at=now,
        updated_at=now,
    )
    db.add(session)
    db.commit()
    return session


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text) // 4)


def extract_tables(query: str) -> list[str]:
    matches = re.findall(
        r"\bfrom\s+([a-zA-Z_][a-zA-Z0-9_]*)|\bjoin\s+([a-zA-Z_][a-zA-Z0-9_]*)",
        query,
    )
    tables = []
    for first, second in matches:
        if first:
            tables.append(first)
        if second:
            tables.append(second)
    return tables


def apply_scope_to_rows(rows: list[dict], scope: dict) -> list[dict]:
    allowed = scope.get("allowed_person_ids")
    if allowed is None:
        return rows
    filtered = []
    for row in rows:
        if "person_id" in row:
            if row["person_id"] in allowed:
                filtered.append(row)
            continue
        if "id" in row and "name" in row and "timestamp" not in row and "check_id" not in row:
            if row["id"] in allowed:
                filtered.append(row)
            continue
        filtered.append(row)
    return filtered


def fetch_available_months(db: Session) -> list[str]:
    rows = (
        db.execute(text("select distinct strftime('%Y-%m', timestamp) as month from checks order by month"))
        .mappings()
        .all()
    )
    return [row["month"] for row in rows if row.get("month")]
