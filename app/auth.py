import hashlib
import hmac
import secrets
from typing import Iterable

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from .db import get_db
from .models import User

PBKDF2_ITERATIONS = 200_000

DEFAULT_ADMIN_USERS: Iterable[tuple[str, str]] = (
    ("professor1", "professor123"),
    ("professor2", "professor123"),
    ("professor3", "professor123"),
)


def hash_password(password: str, salt: str | None = None) -> str:
    if salt is None:
        salt = secrets.token_hex(16)
    hashed = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), PBKDF2_ITERATIONS
    )
    return f"{salt}${hashed.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        salt, hashed = stored_hash.split("$", 1)
    except ValueError:
        return False
    candidate = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), PBKDF2_ITERATIONS
    ).hex()
    return hmac.compare_digest(candidate, hashed)


def seed_admin_users(db: Session) -> list[str]:
    created = []
    for username, password in DEFAULT_ADMIN_USERS:
        existing = db.query(User).filter(User.username == username).first()
        if existing:
            continue
        db.add(
            User(
                username=username,
                password_hash=hash_password(password),
                role="admin",
            )
        )
        created.append(username)
    if created:
        db.commit()
    return created


def login_user(request: Request, user: User) -> None:
    request.session["user_id"] = user.id
    request.session["username"] = user.username


def logout_user(request: Request) -> None:
    request.session.pop("user_id", None)
    request.session.pop("username", None)


def get_current_user(
    request: Request, db: Session = Depends(get_db)
) -> User | None:
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    user = db.get(User, user_id)
    if not user:
        logout_user(request)
    return user
