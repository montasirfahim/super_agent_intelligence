import logging

from sqlalchemy.exc import OperationalError
from sqlmodel import SQLModel

from app.database import engine
from app.models.schema import *

logger = logging.getLogger(__name__)


def create_all_tables() -> None:
    try:
        SQLModel.metadata.create_all(engine)
        logger.info("Database tables created successfully")
    except OperationalError as exc:
        logger.warning("Database unavailable during startup: %s", exc)
