"""Lightweight role/session helper.

Hackathon demo uses query-string identity (?role=agent&user_id=...) instead of
real authentication. Production swap-in: replace this module with JWT/OAuth.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from fastapi import HTTPException, Request


VALID_ROLES = {"agent", "officer", "analyst", "central_ops"}


@dataclass(frozen=True)
class SessionContext:
    role: str
    user_id: str
    display_name: str = ""


def parse_session(request: Request) -> SessionContext:
    """Read ?role=&user_id= from the request. Defaults to 'agent' for the root page."""
    role = request.query_params.get("role", "agent").lower()
    user_id = request.query_params.get("user_id", "")
    if role not in VALID_ROLES:
        raise HTTPException(status_code=400, detail=f"Invalid role: {role}")
    return SessionContext(role=role, user_id=user_id)


def require_role(request: Request, *allowed: str) -> SessionContext:
    ctx = parse_session(request)
    if ctx.role not in allowed:
        raise HTTPException(status_code=403, detail=f"Role {ctx.role} not permitted here")
    return ctx


def query_string_for(ctx: SessionContext, role: Optional[str] = None) -> str:
    """Build ?role=&user_id= snippet for cross-role nav links."""
    r = role or ctx.role
    return f"role={r}&user_id={ctx.user_id}"