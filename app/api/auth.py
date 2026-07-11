"""Authentication endpoints.

Hackathon-grade: password is plaintext column, hardcoded value 123456 across all roles.
Replace with bcrypt/JWT for production.
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlmodel import Session, select

from app.database import get_db
from app.models import Agent, RiskAnalyst, TerritoryOffice


router = APIRouter(tags=["auth"])

DEMO_PASSWORD = "123456"

ROLE_TO_MODEL = {
    "agent": Agent,
    "officer": TerritoryOffice,
    "analyst": RiskAnalyst,
}

ROLE_TO_ID_FIELD = {
    "agent": "agent_id",
    "officer": "id",
    "analyst": "id",
}


@router.post("/api/login")
def login(
    payload: dict = Body(...),
    db: Session = Depends(get_db),
):
    """
    Body: {role: 'agent'|'officer'|'analyst', username: str, password: str}

    Returns: {ok: true, role, user_id, display_name, redirect: '/agent-dash?user_id=...'}
    """
    role = (payload or {}).get("role", "").lower().strip()
    username = (payload or {}).get("username", "").strip()
    password = (payload or {}).get("password", "")

    if role not in ROLE_TO_MODEL:
        raise HTTPException(400, "Invalid role. Choose agent, officer, or analyst.")
    if not username:
        raise HTTPException(400, "Username is required.")
    if not password:
        raise HTTPException(400, "Password is required.")

    Model = ROLE_TO_MODEL[role]
    id_field = ROLE_TO_ID_FIELD[role]

    user = db.exec(
        select(Model).where(Model.username == username)
    ).first()

    if not user:
        raise HTTPException(401, f"Username '{username}' not found for role '{role}'.")
    if getattr(user, "password", None) != password:
        raise HTTPException(401, "Incorrect password.")

    user_id = getattr(user, id_field)
    display_name = getattr(user, "name", None) or getattr(user, "shop_name", user_id)

    redirect_path = {
        "agent": "/agent-dash",
        "officer": "/to-dash",
        "analyst": "/risk-dash",
    }[role]

    return {
        "ok": True,
        "role": role,
        "user_id": user_id,
        "display_name": display_name,
        "redirect": f"{redirect_path}?user_id={user_id}",
    }