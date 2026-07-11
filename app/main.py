from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.router import api_router
from app.startup import create_all_tables


TEMPLATES = Path(__file__).parent / "templates"
STATIC = TEMPLATES / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_all_tables()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Super Agent Intelligence", version="0.2.0", lifespan=lifespan)
    app.include_router(api_router)
    app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")

    @app.get("/", include_in_schema=False)
    async def login_page():
        return FileResponse(TEMPLATES / "login.html")

    @app.get("/agent-dash", include_in_schema=False)
    async def agent_dash_page():
        return FileResponse(TEMPLATES / "agent-dash.html")

    @app.get("/to-dash", include_in_schema=False)
    async def to_dash_page():
        return FileResponse(TEMPLATES / "to-dash.html")

    @app.get("/risk-dash", include_in_schema=False)
    async def risk_dash_page():
        return FileResponse(TEMPLATES / "risk-dash.html")

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        return JSONResponse(status_code=422, content={"error": "validation_error", "detail": exc.errors()})

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        return JSONResponse(status_code=500, content={"error": "internal_server_error", "detail": str(exc)})

    return app


app = create_app()