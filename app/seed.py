from sqlalchemy.orm import Session

from .models import Person
from .services import START_SALARY

DEFAULT_PEOPLE_ROWS = (
    ('宋新雷', '', '', ''),
    ('李丽君', '张梓', '郑柳', '王福州'),
    ('徐梦溪', '陈梦菡', '李浩然', '余宏知'),
    ('郭梦琼', '刘若彤', '白婷婷', '王庆芳'),
    ('夏盛培', '袁晨栋', '黄婧', '闫帆'),
    ('周宁宁', '焦睿哲', '王振宇', '王梦男'),
)

# Keep 4 columns per row for roster layout; empty slots are ignored.
DEFAULT_PEOPLE = [name for row in DEFAULT_PEOPLE_ROWS for name in row if name]

NAME_UPDATES = {
    "张梓斌": "张梓",
    "陈梦涵": "陈梦菡",
    "焦春哲": "焦睿哲",
}


def apply_name_updates(db: Session) -> list[str]:
    people = db.query(Person).all()
    by_name = {person.name: person for person in people}
    updated = []
    for old, new in NAME_UPDATES.items():
        person = by_name.get(old)
        if not person:
            continue
        if new in by_name:
            continue
        person.name = new
        updated.append(new)
    if updated:
        db.commit()
    return updated


def seed_people(db: Session) -> list[str]:
    apply_name_updates(db)
    existing = {row[0] for row in db.query(Person.name).all()}
    created = []
    for name in DEFAULT_PEOPLE:
        if name in existing:
            continue
        db.add(Person(name=name, current_salary=START_SALARY, active=True))
        created.append(name)
    if created:
        db.commit()
    return created
