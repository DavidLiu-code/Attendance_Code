from datetime import datetime
import math
import os
from urllib.parse import urlencode

from fastapi import Body, Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from .auth import get_current_user, login_user, logout_user, seed_admin_users, verify_password
from .seed import seed_people
from .chat_service import handle_chat_request
from .db import SessionLocal, get_db, init_db
from .models import Check, Mark, Person, SalaryHistory, User
from .services import (
    START_SALARY,
    close_month,
    get_timezone,
    is_core_time,
    month_start_end,
    now_local,
    preview_month,
    recalculate_all,
)

app = FastAPI(title="AttendanceHub")
app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("APP_SECRET_KEY", "change-me"),
    session_cookie="attendance_session",
)

templates = Jinja2Templates(directory="app/templates")


@app.on_event("startup")
def on_startup() -> None:
    init_db()
    db = SessionLocal()
    try:
        seed_admin_users(db)
        seed_people(db)
    finally:
        db.close()


def redirect(url: str, msg: str | None = None, error: str | None = None, extra: dict | None = None):
    params = {}
    if extra:
        params.update(extra)
    if msg:
        params["msg"] = msg
    if error:
        params["error"] = error
    if params:
        url = f"{url}?{urlencode(params)}"
    return RedirectResponse(url, status_code=303)


def require_admin(request: Request, current_user: User | None):
    if current_user and current_user.role == "admin":
        return None
    next_path = request.url.path
    if request.url.query:
        next_path = f"{next_path}?{request.url.query}"
    return redirect(
        "/login",
        error="Admin login required.",
        extra={"next": next_path},
    )


def safe_next(next_path: str | None) -> str:
    if next_path and next_path.startswith("/") and not next_path.startswith("//"):
        return next_path
    return "/"


def build_people_cards(
    db: Session,
    total_checks: int,
    checks_start: datetime | None = None,
    checks_end: datetime | None = None,
):
    people = db.query(Person).order_by(Person.name).all()
    absent_query = (
        db.query(Mark.person_id, func.count(Mark.id))
        .filter(Mark.status == "absent")
    )
    marked_query = db.query(Mark.person_id, func.count(Mark.id))
    if checks_start and checks_end:
        absent_query = absent_query.join(Check, Mark.check_id == Check.id).filter(
            Check.timestamp >= checks_start, Check.timestamp < checks_end
        )
        marked_query = marked_query.join(Check, Mark.check_id == Check.id).filter(
            Check.timestamp >= checks_start, Check.timestamp < checks_end
        )

    absent_counts = dict(absent_query.group_by(Mark.person_id).all())
    marked_counts = dict(marked_query.group_by(Mark.person_id).all())
    cards = []
    for person in people:
        absent_count = absent_counts.get(person.id, 0)
        marked_count = marked_counts.get(person.id, 0)
        if total_checks:
            absence_rate = f"{(absent_count / total_checks) * 100:.1f}%"
        else:
            absence_rate = "N/A"
        cards.append(
            {
                "person": person,
                "absent_count": absent_count,
                "absence_rate": absence_rate,
                "missing_count": max(0, total_checks - marked_count),
            }
        )
    return cards


@app.get("/login", response_class=HTMLResponse)
def login_page(
    request: Request,
    current_user: User | None = Depends(get_current_user),
    next: str | None = None,
    msg: str | None = None,
    error: str | None = None,
):
    if current_user:
        return redirect("/", msg="Already signed in.")
    return templates.TemplateResponse(
        "login.html",
        {
            "request": request,
            "next": safe_next(next),
            "current_user": current_user,
            "msg": msg,
            "error": error,
        },
    )


@app.post("/login")
def login_action(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str | None = Form(None),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.username == username.strip()).first()
    if not user or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "next": safe_next(next),
                "current_user": None,
                "error": "Invalid username or password.",
            },
            status_code=401,
        )
    login_user(request, user)
    return redirect(safe_next(next), msg="Signed in.")


@app.post("/logout")
def logout_action(request: Request):
    logout_user(request)
    return redirect("/", msg="Signed out.")


@app.get("/people", response_class=HTMLResponse)
def people_page(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user),
    msg: str | None = None,
    error: str | None = None,
):
    now = now_local()
    current_month = f"{now.year:04d}-{now.month:02d}"
    month_start, month_end = month_start_end(current_month)
    if current_user and current_user.role == "admin":
        total_checks = db.query(func.count(Check.id)).scalar() or 0
        people_cards = build_people_cards(db, total_checks)
        scope_label = "all checks"
    else:
        total_checks = (
            db.query(func.count(Check.id))
            .filter(Check.timestamp >= month_start, Check.timestamp < month_end)
            .scalar()
            or 0
        )
        people_cards = build_people_cards(
            db, total_checks, checks_start=month_start, checks_end=month_end
        )
        people_cards = [card for card in people_cards if card["absent_count"] > 3]
        scope_label = f"{current_month}"
    return templates.TemplateResponse(
        "people.html",
        {
            "request": request,
            "people_cards": people_cards,
            "total_checks": total_checks,
            "scope_label": scope_label,
            "start_salary": START_SALARY,
            "current_user": current_user,
            "msg": msg,
            "error": error,
        },
    )


@app.get("/export", response_class=HTMLResponse)
def export_page(
    request: Request,
    current_user: User | None = Depends(get_current_user),
    msg: str | None = None,
    error: str | None = None,
):
    guard = require_admin(request, current_user)
    if guard:
        return guard
    return templates.TemplateResponse(
        "export.html",
        {
            "request": request,
            "current_user": current_user,
            "msg": msg,
            "error": error,
        },
    )


@app.get("/chat", response_class=HTMLResponse)
def chat_page(
    request: Request,
    current_user: User | None = Depends(get_current_user),
    msg: str | None = None,
    error: str | None = None,
):
    session_id = request.session.get("chat_session_id")
    return templates.TemplateResponse(
        "chat.html",
        {
            "request": request,
            "question": "",
            "answer": None,
            "details": None,
            "clarifying_question": None,
            "data_preview": None,
            "session_id": session_id,
            "current_user": current_user,
            "msg": msg,
            "error": error,
        },
    )


@app.post("/chat", response_class=HTMLResponse)
def chat_action(
    request: Request,
    question: str = Form(...),
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user),
):
    payload = {
        "session_id": request.session.get("chat_session_id"),
        "user_id": current_user.username if current_user else "visitor",
        "role": current_user.role if current_user else "user",
        "message": question,
        "stream": False,
    }
    result = handle_chat_request(db, payload)
    if "error" in result:
        return templates.TemplateResponse(
            "chat.html",
            {
                "request": request,
                "question": question,
                "answer": None,
                "details": None,
                "clarifying_question": None,
                "data_preview": None,
                "session_id": request.session.get("chat_session_id"),
                "current_user": current_user,
                "msg": None,
                "error": result["error"],
            },
        )
    if "session_id" in result:
        request.session["chat_session_id"] = result["session_id"]
    return templates.TemplateResponse(
        "chat.html",
        {
            "request": request,
            "question": question,
            "answer": result.get("answer"),
            "details": result.get("assumptions"),
            "clarifying_question": None,
            "data_preview": result.get("data_preview"),
            "session_id": result.get("session_id"),
            "current_user": current_user,
            "msg": None,
            "error": None,
        },
    )


@app.post("/api/chat")
def api_chat(payload: dict = Body(...), db: Session = Depends(get_db)):
    result = handle_chat_request(db, payload)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@app.get("/", response_class=HTMLResponse)
def home(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user),
    msg: str | None = None,
    error: str | None = None,
):
    now = now_local()
    current_month = f"{now.year:04d}-{now.month:02d}"
    month_start, month_end = month_start_end(current_month)
    total_checks = db.query(func.count(Check.id)).scalar() or 0
    last_check = db.query(Check).order_by(Check.timestamp.desc()).first()
    active_people_count = db.query(func.count(Person.id)).filter(Person.active.is_(True)).scalar() or 0
    month_checks_count = (
        db.query(func.count(Check.id))
        .filter(Check.timestamp >= month_start, Check.timestamp < month_end)
        .scalar()
        or 0
    )
    total_absences_active = (
        db.query(func.count(Mark.id))
        .join(Person, Mark.person_id == Person.id)
        .join(Check, Mark.check_id == Check.id)
        .filter(
            Person.active.is_(True),
            Mark.status == "absent",
            Check.timestamp >= month_start,
            Check.timestamp < month_end,
        )
        .scalar()
        or 0
    )
    if active_people_count and month_checks_count:
        avg_absences_active = (
            total_absences_active / (active_people_count * month_checks_count) * 100
        )
    else:
        avg_absences_active = 0
    absent_marks = (
        db.query(
            Person.id.label("person_id"),
            Person.name.label("name"),
            Check.timestamp.label("timestamp"),
        )
        .join(Mark, Mark.person_id == Person.id)
        .join(Check, Mark.check_id == Check.id)
        .filter(
            Person.active.is_(True),
            Mark.status == "absent",
            Check.timestamp >= month_start,
            Check.timestamp < month_end,
        )
        .all()
    )
    core_absent_counts = {}
    name_map = {}
    for row in absent_marks:
        name_map[row.person_id] = row.name
        if is_core_time(row.timestamp):
            core_absent_counts[row.person_id] = core_absent_counts.get(row.person_id, 0) + 1
    absent_rows = [
        {"name": name_map[person_id], "count": count}
        for person_id, count in core_absent_counts.items()
        if count > 3
    ]
    absent_rows.sort(key=lambda row: row["count"], reverse=True)
    max_count = max((row["count"] for row in absent_rows), default=0)
    absent_stats = [
        {
            "name": row["name"],
            "count": row["count"],
            "width": int((row["count"] / max_count) * 100) if max_count else 0,
        }
        for row in absent_rows
    ]
    return templates.TemplateResponse(
        "home.html",
        {
            "request": request,
            "absent_stats": absent_stats,
            "total_checks": total_checks,
            "last_check": last_check,
            "total_absences_active": total_absences_active,
            "avg_absences_active": avg_absences_active,
            "current_month": current_month,
            "current_user": current_user,
            "msg": msg,
            "error": error,
        },
    )


@app.post("/people")
def add_person(
    request: Request,
    name: str = Form(...),
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user),
):
    guard = require_admin(request, current_user)
    if guard:
        return guard
    clean_name = name.strip()
    if not clean_name:
        return redirect("/people", error="Name is required.")

    existing = db.query(Person).filter(Person.name == clean_name).first()
    if existing:
        return redirect("/people", error="Name already exists.")

    person = Person(name=clean_name, current_salary=START_SALARY, active=True)
    db.add(person)
    db.commit()
    return redirect("/people", msg="Person added.")


@app.post("/people/{person_id}/delete")
def delete_person(
    person_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user),
):
    guard = require_admin(request, current_user)
    if guard:
        return guard
    person = db.get(Person, person_id)
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")
    db.query(Mark).filter(Mark.person_id == person_id).delete(synchronize_session=False)
    db.query(SalaryHistory).filter(SalaryHistory.person_id == person_id).delete(
        synchronize_session=False
    )
    db.delete(person)
    db.commit()
    return redirect("/people", msg=f"{person.name} deleted.")


@app.post("/people/{person_id}/toggle")
def toggle_person(
    person_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user),
):
    guard = require_admin(request, current_user)
    if guard:
        return guard
    person = db.get(Person, person_id)
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")
    person.active = not person.active
    db.commit()
    state = "activated" if person.active else "deactivated"
    return redirect("/people", msg=f"{person.name} {state}.")


@app.get("/people/{person_id}", response_class=HTMLResponse)
def person_history(
    request: Request,
    person_id: int,
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user),
    status: str | None = None,
    msg: str | None = None,
    error: str | None = None,
):
    person = db.get(Person, person_id)
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")
    if status not in (None, "present", "absent", "unmarked", "infraction"):
        status = None
    history = (
        db.query(SalaryHistory)
        .filter(SalaryHistory.person_id == person_id)
        .order_by(SalaryHistory.month)
        .all()
    )
    if not history:
        from .services import (
            apply_month_change,
            collect_marks_for_checks,
            get_checks_for_month,
            is_perfect_month,
            list_months_with_checks,
        )

        months = list_months_with_checks(db)
        if months:
            computed_at = now_local()
            current_salary = START_SALARY
            computed_history = []
            for month in months:
                checks = get_checks_for_month(db, month)
                marks_by_check = collect_marks_for_checks(
                    db, [check.id for check in checks]
                )
                perfect = is_perfect_month(checks, marks_by_check, person.id)
                delta, salary_after, reason = apply_month_change(current_salary, perfect)
                computed_history.append(
                    SalaryHistory(
                        person_id=person.id,
                        month=month,
                        salary_before=current_salary,
                        delta=delta,
                        salary_after=salary_after,
                        reason=reason,
                        computed_at=computed_at,
                    )
                )
                current_salary = salary_after
            history = computed_history
    checks = db.query(Check).order_by(Check.timestamp.desc()).all()
    marks = db.query(Mark).filter(Mark.person_id == person_id).all()
    mark_map = {mark.check_id: mark.status for mark in marks}
    attendance_rows = [
        {
            "check": check,
            "status": mark_map.get(check.id, "unmarked"),
            "time_bucket": "core" if is_core_time(check.timestamp) else "flex",
        }
        for check in checks
    ]
    attendance_total = len(attendance_rows)
    attendance_min_height = (attendance_total + 1) * 44 if attendance_total else 0
    return templates.TemplateResponse(
        "person_history.html",
        {
            "request": request,
            "person": person,
            "history": history,
            "attendance_rows": attendance_rows,
            "attendance_min_height": attendance_min_height,
            "filter_status": status,
            "current_user": current_user,
            "msg": msg,
            "error": error,
        },
    )


@app.get("/checks", response_class=HTMLResponse)
def list_checks(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user),
    page: int = 1,
    month: str | None = None,
    msg: str | None = None,
    error: str | None = None,
):
    guard = require_admin(request, current_user)
    if guard:
        return guard
    page_size = 20
    query = db.query(Check)

    if month:
        try:
            start, end = month_start_end(month)
        except ValueError:
            return redirect("/checks", error="Invalid month format. Use YYYY-MM.")
        query = query.filter(Check.timestamp >= start, Check.timestamp < end)

    total = query.count()
    total_pages = max(1, math.ceil(total / page_size)) if total else 1
    page = max(1, min(page, total_pages))
    checks = (
        query.order_by(Check.timestamp.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    total_people = db.query(Person).filter(Person.active.is_(True)).count()
    check_ids = [check.id for check in checks]
    counts = {check_id: {"present": 0, "absent": 0, "marked": 0} for check_id in check_ids}
    if check_ids:
        marks = (
            db.query(Mark)
            .join(Person, Mark.person_id == Person.id)
            .filter(Mark.check_id.in_(check_ids), Person.active.is_(True))
            .all()
        )
        for mark in marks:
            if mark.check_id not in counts:
                continue
            counts[mark.check_id]["marked"] += 1
            if mark.status in ("present", "absent"):
                counts[mark.check_id][mark.status] += 1

    rows = []
    for check in checks:
        present = counts.get(check.id, {}).get("present", 0)
        absent = counts.get(check.id, {}).get("absent", 0)
        marked = counts.get(check.id, {}).get("marked", 0)
        unmarked = max(0, total_people - marked)
        rows.append(
            {
                "check": check,
                "present": present,
                "absent": absent,
                "unmarked": unmarked,
            }
        )

    tz = get_timezone()
    timezone_name = getattr(tz, "key", str(tz))

    return templates.TemplateResponse(
        "checks.html",
        {
            "request": request,
            "checks": rows,
            "page": page,
            "total_pages": total_pages,
            "month": month,
            "timezone_name": timezone_name,
            "current_user": current_user,
            "msg": msg,
            "error": error,
        },
    )


@app.post("/checks")
def create_check(
    request: Request,
    timestamp: str | None = Form(None),
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user),
):
    guard = require_admin(request, current_user)
    if guard:
        return guard
    if timestamp:
        try:
            parsed = datetime.fromisoformat(timestamp)
        except ValueError:
            return redirect("/checks", error="Invalid timestamp.")
    else:
        parsed = now_local()

    created_by = current_user.username if current_user else None
    check = Check(timestamp=parsed, created_by=created_by)
    db.add(check)
    db.commit()
    return redirect(f"/checks/{check.id}", msg="Check created.")


@app.get("/checks/{check_id}", response_class=HTMLResponse)
def check_detail(
    request: Request,
    check_id: int,
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user),
    origin: str | None = None,
    person_id: int | None = None,
    status: str | None = None,
    msg: str | None = None,
    error: str | None = None,
):
    guard = require_admin(request, current_user)
    if guard:
        return guard
    check = db.get(Check, check_id)
    if not check:
        raise HTTPException(status_code=404, detail="Check not found")

    people = db.query(Person).filter(Person.active.is_(True)).order_by(Person.name).all()
    marks = (
        db.query(Mark)
        .join(Person, Mark.person_id == Person.id)
        .filter(Mark.check_id == check_id, Person.active.is_(True))
        .all()
    )
    mark_map = {mark.person_id: mark.status for mark in marks}
    unmarked_count = max(0, len(people) - len(marks))
    back_to_people_url = None
    if origin == "people" and person_id:
        back_to_people_url = f"/people/{person_id}"
        if status in ("present", "absent", "unmarked", "infraction"):
            back_to_people_url = f"{back_to_people_url}?status={status}"

    return templates.TemplateResponse(
        "check_detail.html",
        {
            "request": request,
            "check": check,
            "people": people,
            "marks": mark_map,
            "unmarked_count": unmarked_count,
            "back_to_people_url": back_to_people_url,
            "current_user": current_user,
            "msg": msg,
            "error": error,
        },
    )


@app.post("/checks/{check_id}/marks")
async def update_marks(
    check_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user),
):
    guard = require_admin(request, current_user)
    if guard:
        return guard
    check = db.get(Check, check_id)
    if not check:
        raise HTTPException(status_code=404, detail="Check not found")

    form = await request.form()
    people = db.query(Person).filter(Person.active.is_(True)).all()
    existing_marks = (
        db.query(Mark)
        .join(Person, Mark.person_id == Person.id)
        .filter(Mark.check_id == check_id, Person.active.is_(True))
        .all()
    )
    mark_map = {mark.person_id: mark for mark in existing_marks}

    for person in people:
        key = f"mark_{person.id}"
        value = form.get(key)
        if value in (None, "", "unmarked"):
            if person.id in mark_map:
                db.delete(mark_map[person.id])
            continue

        if value not in ("present", "absent", "infraction"):
            continue

        if person.id in mark_map:
            mark_map[person.id].status = value
        else:
            db.add(Mark(check_id=check_id, person_id=person.id, status=value))

    db.commit()
    return redirect(f"/checks/{check_id}", msg="Marks saved.")


@app.post("/checks/{check_id}/delete")
def delete_check(
    check_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user),
):
    guard = require_admin(request, current_user)
    if guard:
        return guard
    check = db.get(Check, check_id)
    if not check:
        raise HTTPException(status_code=404, detail="Check not found")
    db.delete(check)
    db.commit()
    return redirect("/checks", msg="Check deleted.")


@app.get("/months/close", response_class=HTMLResponse)
def month_close_page(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user),
    month: str | None = None,
    msg: str | None = None,
    error: str | None = None,
):
    guard = require_admin(request, current_user)
    if guard:
        return guard
    preview = None
    already_closed = False

    if month:
        try:
            preview = preview_month(db, month)
            already_closed = (
                db.query(SalaryHistory).filter(SalaryHistory.month == month).first()
                is not None
            )
        except ValueError as exc:
            error = str(exc)

    return templates.TemplateResponse(
        "month_close.html",
        {
            "request": request,
            "month": month,
            "preview": preview,
            "already_closed": already_closed,
            "current_user": current_user,
            "msg": msg,
            "error": error,
        },
    )


@app.post("/months/close")
def month_close_action(
    request: Request,
    month: str = Form(...),
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user),
):
    guard = require_admin(request, current_user)
    if guard:
        return guard
    try:
        months = close_month(db, month)
    except ValueError as exc:
        return redirect("/months/close", error=str(exc), extra={"month": month})
    return redirect(
        "/months/close",
        msg=f"Closed {month} (recomputed {months} month(s)).",
        extra={"month": month},
    )


@app.post("/months/recalculate")
def month_recalculate(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user),
):
    guard = require_admin(request, current_user)
    if guard:
        return guard
    months = recalculate_all(db)
    return redirect("/months/close", msg=f"Recalculated {months} month(s).")


@app.get("/export/salary_history.csv")
def export_salary_history(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user),
):
    guard = require_admin(request, current_user)
    if guard:
        return guard
    rows = (
        db.query(SalaryHistory, Person)
        .join(Person, SalaryHistory.person_id == Person.id)
        .order_by(SalaryHistory.month, Person.name)
        .all()
    )

    import csv
    import io
    from .services import get_checks_for_month, list_months_with_checks, collect_marks_for_checks, is_perfect_month, apply_month_change

    def generate():
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                "person",
                "month",
                "salary_before",
                "delta",
                "salary_after",
                "reason",
                "computed_at",
            ]
        )
        yield output.getvalue()
        output.seek(0)
        output.truncate(0)

        if rows:
            for history, person in rows:
                writer.writerow(
                    [
                        person.name,
                        history.month,
                        history.salary_before,
                        history.delta,
                        history.salary_after,
                        history.reason,
                        history.computed_at.isoformat(sep=" "),
                    ]
                )
                yield output.getvalue()
                output.seek(0)
                output.truncate(0)
            return

        months = list_months_with_checks(db)
        people = db.query(Person).order_by(Person.name).all()
        salary_map = {person.id: START_SALARY for person in people}
        computed_at = now_local()
        for month in months:
            checks = get_checks_for_month(db, month)
            marks_by_check = collect_marks_for_checks(db, [check.id for check in checks])
            for person in people:
                salary_before = salary_map[person.id]
                perfect = is_perfect_month(checks, marks_by_check, person.id)
                delta, salary_after, reason = apply_month_change(salary_before, perfect)
                writer.writerow(
                    [
                        person.name,
                        month,
                        salary_before,
                        delta,
                        salary_after,
                        reason,
                        computed_at.isoformat(sep=" "),
                    ]
                )
                salary_map[person.id] = salary_after
                yield output.getvalue()
                output.seek(0)
                output.truncate(0)

    headers = {"Content-Disposition": "attachment; filename=salary_history.csv"}
    return StreamingResponse(generate(), media_type="text/csv", headers=headers)


@app.get("/export/attendance.csv")
def export_attendance(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user),
):
    guard = require_admin(request, current_user)
    if guard:
        return guard
    checks = db.query(Check).order_by(Check.timestamp).all()
    people = db.query(Person).order_by(Person.name).all()
    marks = db.query(Mark).all()
    mark_map = {(mark.check_id, mark.person_id): mark.status for mark in marks}

    import csv
    import io

    def generate():
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                "check_id",
                "check_timestamp",
                "check_created_by",
                "person",
                "person_active",
                "status",
            ]
        )
        yield output.getvalue()
        output.seek(0)
        output.truncate(0)

        for check in checks:
            ts = check.timestamp.isoformat(sep=" ")
            for person in people:
                status = mark_map.get((check.id, person.id), "unmarked")
                writer.writerow(
                    [
                        check.id,
                        ts,
                        check.created_by or "Unknown",
                        person.name,
                        "active" if person.active else "inactive",
                        status,
                    ]
                )
                yield output.getvalue()
                output.seek(0)
                output.truncate(0)

    headers = {"Content-Disposition": "attachment; filename=attendance.csv"}
    return StreamingResponse(generate(), media_type="text/csv", headers=headers)
