import os

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker


def build_database_url(base_url: str, password: str | None) -> str:
    if not password:
        return base_url

    prefix, _, suffix = base_url.partition("://")
    if not suffix:
        return base_url

    credentials, separator, remainder = suffix.partition("@")
    if not separator:
        return base_url

    userinfo = credentials.split(":", 1)
    if len(userinfo) == 2 and userinfo[1]:
        return base_url

    return f"{prefix}://{userinfo[0]}:{password}@{remainder}"


DATABASE_URL = build_database_url(
    os.getenv("DATABASE_URL", "mysql+pymysql://root@localhost:3306/super_agent_intelligence"),
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
