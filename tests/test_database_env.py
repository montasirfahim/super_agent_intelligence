from app.database import build_database_url


def test_build_database_url_injects_password_from_env():
    url = build_database_url("mysql+pymysql://root@localhost:3306/super_agent_intelligence", "secret123")
    assert url == "mysql+pymysql://root:secret123@localhost:3306/super_agent_intelligence"
