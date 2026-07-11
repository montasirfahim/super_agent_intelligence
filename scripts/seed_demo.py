from pathlib import Path

from app.main import app

print("Seed script initialized for", app.title)
print("Template available at", Path("app/templates/index.html").resolve())
