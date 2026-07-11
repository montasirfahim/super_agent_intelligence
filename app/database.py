import os

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker


def build_database_url(base_url: str, password: str | None) -> str:
    if not password:
        return base_url

    if "://" not in base_url:
        return base_url

    prefix, rest = base_url.split("://", 1)
    if "@" not in rest:
        return base_url

    credentials, host_and_db = rest.split("@", 1)
    if ":" in credentials:
        return base_url

    return f"{prefix}://{credentials}:{password}@{host_and_db}"


DATABASE_URL = build_database_url(
    os.getenv("DATABASE_URL", "mysql+pymysql://root@localhost:3307/super_agent_intelligence"),
    os.getenv("DB_PASS"),
)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
