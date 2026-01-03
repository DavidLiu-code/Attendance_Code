from datetime import datetime
import math
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from .db import get_db, init_db
from .models import Check, Mark, Person, SalaryHistory
from .services import (
    START_SALARY,
    close_month,
    get_timezone,
    month_start_end,
    now_local,
    preview_month,
    recalculate_all,
)

app = FastAPI(title="Attendance + Salary")

templates = Jinja2Templates(directory="app/templates")


@app.on_event("startup")
def on_startup() -> None:
    init_db()


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


@app.get("/", response_class=HTMLResponse)
def home(
    request: Request,
    db: Session = Depends(get_db),
    msg: str | None = None,
    error: str | None = None,
):
    people = db.query(Person).order_by(Person.name).all()
    return templates.TemplateResponse(
        "home.html",
        {
            "request": request,
            "people": people,
            "start_salary": START_SALARY,
            "msg": msg,
            "error": error,
        },
    )


@app.post("/people")
def add_person(name: str = Form(...), db: Session = Depends(get_db)):
    clean_name = name.strip()
    if not clean_name:
        return redirect("/", error="Name is required.")

    existing = db.query(Person).filter(Person.name == clean_name).first()
    if existing:
        return redirect("/", error="Name already exists.")

    person = Person(name=clean_name, current_salary=START_SALARY, active=True)
    db.add(person)
    db.commit()
    return redirect("/", msg="Person added.")


@app.post("/people/{person_id}/toggle")
def toggle_person(person_id: int, db: Session = Depends(get_db)):
    person = db.get(Person, person_id)
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")
    person.active = not person.active
    db.commit()
    state = "activated" if person.active else "deactivated"
    return redirect("/", msg=f"{person.name} {state}.")


@app.get("/people/{person_id}", response_class=HTMLResponse)
def person_history(
    request: Request,
    person_id: int,
    db: Session = Depends(get_db),
    msg: str | None = None,
    error: str | None = None,
):
    person = db.get(Person, person_id)
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")
    history = (
        db.query(SalaryHistory)
        .filter(SalaryHistory.person_id == person_id)
        .order_by(SalaryHistory.month)
        .all()
    )
    return templates.TemplateResponse(
        "person_history.html",
        {
            "request": request,
            "person": person,
            "history": history,
            "msg": msg,
            "error": error,
        },
    )


@app.get("/checks", response_class=HTMLResponse)
def list_checks(
    request: Request,
    db: Session = Depends(get_db),
    page: int = 1,
    month: str | None = None,
    msg: str | None = None,
    error: str | None = None,
):
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

    total_people = db.query(Person).count()
    check_ids = [check.id for check in checks]
    counts = {check_id: {"present": 0, "absent": 0} for check_id in check_ids}
    if check_ids:
        marks = db.query(Mark).filter(Mark.check_id.in_(check_ids)).all()
        for mark in marks:
            if mark.status in counts[mark.check_id]:
                counts[mark.check_id][mark.status] += 1

    rows = []
    for check in checks:
        present = counts.get(check.id, {}).get("present", 0)
        absent = counts.get(check.id, {}).get("absent", 0)
        marked = present + absent
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
            "msg": msg,
            "error": error,
        },
    )


@app.post("/checks")
def create_check(timestamp: str | None = Form(None), db: Session = Depends(get_db)):
    if timestamp:
        try:
            parsed = datetime.fromisoformat(timestamp)
        except ValueError:
            return redirect("/checks", error="Invalid timestamp.")
    else:
        parsed = now_local()

    check = Check(timestamp=parsed)
    db.add(check)
    db.commit()
    return redirect(f"/checks/{check.id}", msg="Check created.")


@app.get("/checks/{check_id}", response_class=HTMLResponse)
def check_detail(
    request: Request,
    check_id: int,
    db: Session = Depends(get_db),
    msg: str | None = None,
    error: str | None = None,
):
    check = db.get(Check, check_id)
    if not check:
        raise HTTPException(status_code=404, detail="Check not found")

    people = db.query(Person).order_by(Person.name).all()
    marks = db.query(Mark).filter(Mark.check_id == check_id).all()
    mark_map = {mark.person_id: mark.status for mark in marks}
    unmarked_count = max(0, len(people) - len(marks))

    return templates.TemplateResponse(
        "check_detail.html",
        {
            "request": request,
            "check": check,
            "people": people,
            "marks": mark_map,
            "unmarked_count": unmarked_count,
            "msg": msg,
            "error": error,
        },
    )


@app.post("/checks/{check_id}/marks")
async def update_marks(
    check_id: int, request: Request, db: Session = Depends(get_db)
):
    check = db.get(Check, check_id)
    if not check:
        raise HTTPException(status_code=404, detail="Check not found")

    form = await request.form()
    people = db.query(Person).all()
    existing_marks = db.query(Mark).filter(Mark.check_id == check_id).all()
    mark_map = {mark.person_id: mark for mark in existing_marks}

    for person in people:
        key = f"mark_{person.id}"
        value = form.get(key)
        if value in (None, "", "unmarked"):
            if person.id in mark_map:
                db.delete(mark_map[person.id])
            continue

        if value not in ("present", "absent"):
            continue

        if person.id in mark_map:
            mark_map[person.id].status = value
        else:
            db.add(Mark(check_id=check_id, person_id=person.id, status=value))

    db.commit()
    return redirect(f"/checks/{check_id}", msg="Marks saved.")


@app.post("/checks/{check_id}/delete")
def delete_check(check_id: int, db: Session = Depends(get_db)):
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
    month: str | None = None,
    msg: str | None = None,
    error: str | None = None,
):
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
            "msg": msg,
            "error": error,
        },
    )


@app.post("/months/close")
def month_close_action(month: str = Form(...), db: Session = Depends(get_db)):
    try:
        close_month(db, month)
    except ValueError as exc:
        return redirect("/months/close", error=str(exc), extra={"month": month})
    return redirect("/months/close", msg="Month closed.", extra={"month": month})


@app.post("/months/recalculate")
def month_recalculate(db: Session = Depends(get_db)):
    months = recalculate_all(db)
    return redirect("/months/close", msg=f"Recalculated {months} month(s).")


@app.get("/export/salary_history.csv")
def export_salary_history(db: Session = Depends(get_db)):
    rows = (
        db.query(SalaryHistory, Person)
        .join(Person, SalaryHistory.person_id == Person.id)
        .order_by(SalaryHistory.month, Person.name)
        .all()
    )

    import csv
    import io

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

    headers = {"Content-Disposition": "attachment; filename=salary_history.csv"}
    return StreamingResponse(generate(), media_type="text/csv", headers=headers)
