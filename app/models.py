from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, UniqueConstraint
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


class Check(Base):
    __tablename__ = "checks"

    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime, nullable=False)
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
