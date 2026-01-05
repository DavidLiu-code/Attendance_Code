from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import relationship

from .db import Base


class Person(Base):
    __tablename__ = "people"

    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    active = Column(Boolean, default=True, nullable=False)
    current_salary = Column(Integer, default=50, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    marks = relationship("Mark", back_populates="person")
    salary_history = relationship("SalaryHistory", back_populates="person")


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False)
    password_hash = Column(String, nullable=False)
    role = Column(String, default="admin", nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class Check(Base):
    __tablename__ = "checks"

    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime, nullable=False)
    created_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    marks = relationship("Mark", back_populates="check", cascade="all, delete-orphan")


class Mark(Base):
    __tablename__ = "marks"

    id = Column(Integer, primary_key=True)
    check_id = Column(Integer, ForeignKey("checks.id", ondelete="CASCADE"), nullable=False)
    person_id = Column(Integer, ForeignKey("people.id", ondelete="CASCADE"), nullable=False)
    status = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    check = relationship("Check", back_populates="marks")
    person = relationship("Person", back_populates="marks")

    __table_args__ = (
        UniqueConstraint("check_id", "person_id", name="uq_mark_check_person"),
    )


class SalaryHistory(Base):
    __tablename__ = "salary_history"

    id = Column(Integer, primary_key=True)
    person_id = Column(Integer, ForeignKey("people.id", ondelete="CASCADE"), nullable=False)
    month = Column(String, nullable=False)
    salary_before = Column(Integer, nullable=False)
    delta = Column(Integer, nullable=False)
    salary_after = Column(Integer, nullable=False)
    reason = Column(String, nullable=False)
    computed_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    person = relationship("Person", back_populates="salary_history")

    __table_args__ = (
        UniqueConstraint("person_id", "month", name="uq_salary_person_month"),
    )


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    session_id = Column(String, primary_key=True)
    user_id = Column(String, nullable=False)
    role = Column(String, nullable=False)
    default_model_key = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True)
    session_id = Column(String, ForeignKey("chat_sessions.session_id", ondelete="CASCADE"), nullable=False)
    role = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    token_estimate = Column(Integer, default=0, nullable=False)
    meta_json = Column(Text, nullable=True)


class ChatSummary(Base):
    __tablename__ = "chat_summaries"

    session_id = Column(String, ForeignKey("chat_sessions.session_id", ondelete="CASCADE"), primary_key=True)
    summary_text = Column(Text, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    version = Column(Integer, default=1, nullable=False)


class ChatEmbedding(Base):
    __tablename__ = "chat_embeddings"

    id = Column(Integer, primary_key=True)
    message_id = Column(Integer, ForeignKey("chat_messages.id", ondelete="CASCADE"), nullable=False)
    content = Column(Text, nullable=False)
    tokens_json = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class ChatUserScope(Base):
    __tablename__ = "chat_user_scopes"

    id = Column(Integer, primary_key=True)
    user_id = Column(String, nullable=False)
    person_id = Column(Integer, ForeignKey("people.id", ondelete="CASCADE"), nullable=False)
    can_view_salary = Column(Boolean, default=False, nullable=False)

    __table_args__ = (
        UniqueConstraint("user_id", "person_id", name="uq_chat_user_scope"),
    )
