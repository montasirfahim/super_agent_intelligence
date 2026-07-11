from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse

from app.api.router import api_router
from app.startup import create_all_tables


def create_app() -> FastAPI:
    app = FastAPI(title="Super Agent Intelligence", version="0.1.0")
    app.include_router(api_router)

    @app.on_event("startup")
    def startup_event() -> None:
        create_all_tables()

    @app.get("/", include_in_schema=False)
    async def root_page():
        template_path = Path(__file__).parent / "templates" / "index.html"
        return FileResponse(template_path)

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        return JSONResponse(status_code=422, content={"error": "validation_error", "detail": exc.errors()})

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        return JSONResponse(status_code=500, content={"error": "internal_server_error", "detail": str(exc)})

    return app


app = create_app()
