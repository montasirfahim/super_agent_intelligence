import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import Session as SQLModelSession, SQLModel
from dotenv import load_dotenv

# load .env file
load_dotenv()

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

#dot 
RAW_DATABASE_URL = os.getenv("DATABASE_URL")

if not RAW_DATABASE_URL:
    raise ValueError("❌ ERROR: DATABASE_URL environment variable not finding")

DATABASE_URL = build_database_url(
    RAW_DATABASE_URL,
    os.getenv("DB_PASS"),
)

if "sslmode" not in DATABASE_URL:
    if "?" in DATABASE_URL:
        DATABASE_URL += "&sslmode=require"
    else:
        DATABASE_URL += "?sslmode=require"

# SQLModel -> postgresql+psycopg2 ensure prefix
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg2://", 1)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
# Use SQLModel's Session so .exec(select(...)) works everywhere
SessionLocal = sessionmaker(
    class_=SQLModelSession,
    autocommit=False,
    autoflush=False,
    bind=engine,
    expire_on_commit=False,
)

def create_all_tables():
    SQLModel.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()