from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import declarative_base, sessionmaker

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "app.db"

DATABASE_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    import app.models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        columns = conn.execute(text("PRAGMA table_info(checks)")).fetchall()
        column_names = {row[1] for row in columns}
        if "created_by" not in column_names:
            conn.execute(text("ALTER TABLE checks ADD COLUMN created_by TEXT"))


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
