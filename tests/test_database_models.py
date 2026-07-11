from app.database import Base, get_db
from app.models import User


def test_database_and_models_are_available():
    assert Base is not None
    assert User.__tablename__ == "users"
    assert callable(get_db)
