"""Authentication endpoints — backed by base_dataset.json (static).

Hackathon-grade: any agent_id / officer-name / analyst-name from the JSON is valid,
password is hardcoded '123456'. Replace with bcrypt/JWT against the database for
production.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlmodel import Session

from app.database import get_db


router = APIRouter(tags=["auth"])

DEMO_PASSWORD = "123456"

# Map UI role → JSON top-level key
JSON_ROLE_KEY = {
    "agent": "agent",
    "officer": "territory-officer",
    "analyst": "risk-analyst",
}

# Map UI role → fields the user might type at login.
# We try every field in order; first case-insensitive match wins.
# "Treat username, id, agent_id, name all the same" — a user shouldn't have to
# remember whether their handle is stored as "name", "id", or "agent_id".
USERNAME_FIELDS = {
    "agent":   ["agent_id", "id", "username", "name"],
    "officer": ["id", "name", "username"],
    "analyst": ["id", "name", "username"],
}

# Map UI role → the canonical unique-id field for that record
ID_FIELD = {
    "agent": "agent_id",
    "officer": "id",
    "analyst": "id",
}

# Redirect path per role
REDIRECT = {
    "agent": "/agent-dash",
    "officer": "/to-dash",
    "analyst": "/risk-dash",
}


@lru_cache(maxsize=1)
def load_base_dataset() -> dict:
    """Load and cache the static JSON dataset once per process."""
    path = Path(__file__).resolve().parent.parent.parent / "base_dataset.json"
    if not path.exists():
        raise HTTPException(500, f"base_dataset.json not found at {path}")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


@router.post("/api/login")
def login(
    payload: dict = Body(...),
    db: Session = Depends(get_db),  # kept for future use; login itself is JSON-backed
):
    """
    Body: {role: 'agent'|'officer'|'analyst', username: str, password: str}

    The 'username' field is matched (case-insensitive) against any of the
    record's identifier fields, in this order:
      - agent   →  agent_id, id, username, name   (e.g. "agent1", "agent1000")
      - officer →  id, name, username             (e.g. "1", "to1")
      - analyst →  id, name, username             (e.g. "1", "risk1")
    So users can type the agent_id, the name, or the numeric id — any one wins.

    Demo password is '123456'.

    Returns a 'profile' dict shaped for the receiving dashboard:
      - agent   → shop_name, area, district, shared_physical_cash (from JSON)
      - officer → name, provider_id, div_id, area_name
      - analyst → name, provider_id, div_id
    """
    role = (payload or {}).get("role", "").lower().strip()
    username = (payload or {}).get("username", "").strip()
    password = (payload or {}).get("password", "")

    if role not in JSON_ROLE_KEY:
        raise HTTPException(400, "Invalid role. Choose agent, officer, or analyst.")
    if not username:
        raise HTTPException(400, "Username is required.")
    if not password:
        raise HTTPException(400, "Password is required.")
    if password != DEMO_PASSWORD:
        raise HTTPException(401, "Incorrect password.")

    data = load_base_dataset()
    json_key = JSON_ROLE_KEY[role]
    records = data.get(json_key, [])
    username_lower = username.lower()

    # Try every plausible identifier field; first match wins.
    match = None
    matched_field = None
    for r in records:
        for field in USERNAME_FIELDS[role]:
            val = r.get(field)
            if val is None:
                continue
            if str(val).lower() == username_lower:
                match = r
                matched_field = field
                break
        if match:
            break

    if not match:
        raise HTTPException(401, f"No {role} found with username '{username}'.")

    user_id = match.get(ID_FIELD[role])
    # Pretty display name: officers/analysts use "name", agents fall back to shop_name
    if role == "agent":
        display_name = match.get("shop_name") or match.get("agent_id")
    else:
        display_name = match.get("name") or user_id

    # Build a role-tailored profile so the dashboard can render a header chip
    # and (for agents) the physical-cash drawer amount from the static JSON.
    if role == "agent":
        # shared_physical_cash is stored as string ("500000.00") — coerce to float
        spc_raw = match.get("shared_physical_cash", 0)
        try:
            shared_physical_cash = float(spc_raw)
        except (TypeError, ValueError):
            shared_physical_cash = 0.0

        # Pull this agent's provider wallets from base_dataset.json's "providerwallet"
        # array. Each entry has wallet_id, agent_id, provider_id, e_money_balance,
        # last_sync_time. We pass them through so the dashboard can render per-provider
        # live ledgers (JSON-sourced starting balances + live txn deltas).
        agent_id = match.get("agent_id")
        provider_wallets = []
        for w in data.get("providerwallet", []):
            if w.get("agent_id") != agent_id:
                continue
            try:
                bal = float(w.get("e_money_balance", 0))
            except (TypeError, ValueError):
                bal = 0.0
            provider_wallets.append({
                "wallet_id":      w.get("wallet_id"),
                "provider_id":    w.get("provider_id"),       # "bkash"/"nagad"/"rocket"
                "e_money_balance": bal,
                "e_money_balance_fmt": f"৳{int(bal):,}",
                "last_sync_time": w.get("last_sync_time"),
                "source":         "base_dataset.json",
            })

        profile = {
            "agent_id": agent_id,
            "shop_name": match.get("shop_name"),
            "area": match.get("area"),
            "district": match.get("district"),
            "status": match.get("status"),
            "shared_physical_cash": shared_physical_cash,
            "shared_physical_cash_fmt": f"৳{int(shared_physical_cash):,}",
            "provider_wallets": provider_wallets,
            "source": "base_dataset.json",
        }
    elif role == "officer":
        profile = {
            "office_id": match.get("id"),
            "name": match.get("name"),
            "provider_id": match.get("provider_id"),
            "div_id": match.get("div_id"),
            "area_name": match.get("area_name"),
            "risk_analyst_id": match.get("risk_analyst_id"),
            "source": "base_dataset.json",
        }
    else:  # analyst
        profile = {
            "analyst_id": match.get("id"),
            "name": match.get("name"),
            "provider_id": match.get("provider_id"),
            "div_id": match.get("div_id"),
            "source": "base_dataset.json",
        }

    return {
        "ok": True,
        "role": role,
        "user_id": user_id,
        "display_name": display_name,
        "matched_field": matched_field,
        # Include role in the redirect so the dashboard's require_role() check passes.
        "redirect": f"{REDIRECT[role]}?role={role}&user_id={user_id}",
        "profile": profile,
    }


@router.get("/api/login/sample-credentials")
def sample_credentials():
    """Return one sample username per role for the login page hint."""
    data = load_base_dataset()
    def first_username(key: str, field: str):
        return next((r[field] for r in data.get(key, []) if r.get(field)), None)
    return {
        "agent": first_username("agent", "agent_id"),
        "officer": first_username("territory-officer", "name"),
        "analyst": first_username("risk-analyst", "name"),
    }