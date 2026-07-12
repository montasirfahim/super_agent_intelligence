"""Alert + ticket orchestration endpoint.

The analytics engine itself is pure compute — it returns ORM-ready dicts
but does not write. This module wires the engine into a real request that:

  1. Validates the caller (analyst / officer) is allowed to trigger an
     evaluation.
  2. Runs `build_alert_and_tickets(db, agent_id, t, providers)`.
  3. Persists the `Alert` row + one `Ticket` row per responsible provider
     + an initial `AuditLog("CREATED", SYSTEM, ...)` row per ticket.
  4. Returns the created alert + ticket ids so the UI can navigate.

Provider isolation is enforced inside `build_ticket` (via
`_assert_no_cross_provider_leak`), so a regression that re-introduced a
shared-cash leak would raise ValueError here before anything hit the DB.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel import Session

from app.core.session import require_role
from app.database import get_db
from app.models import (
    Alert,
    AlertType,
    AuditAction,
    AuditLog,
    SeverityLevel,
    Ticket,
    TicketStatus,
    OwnerRole,
)
from app.services.analytics_engine import build_alert_and_tickets


router = APIRouter(tags=["alerts"])


@router.post("/api/analytics/evaluate")
def evaluate(
    payload: Dict[str, Any],
    request: Request,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Trigger one evaluation cycle for an agent and persist the results.

    Body:
        {
            "agent_id": "agent1000",       # required
            "providers": ["1", "2"] | null # optional, auto-detected if null
        }

    Returns:
        { ok, alert_id|null, tickets: [{ticket_id, provider_id}, ...] }

    Allowed callers: analyst, officer. Agents must NOT be able to fire
    their own evaluations (would defeat the purpose of the platform).
    """
    ctx = require_role(request, "analyst", "officer")

    agent_id: Optional[str] = payload.get("agent_id")
    if not agent_id:
        raise HTTPException(status_code=400, detail="agent_id required")

    providers: Optional[List[str]] = payload.get("providers")
    # BD-local time to match seeded data + simulation timestamp convention.
    # The engine queries the DB with naive `t-30min <= timestamp <= t` and the
    # seeded/sim txns use BD-local naive datetimes; passing UTC `t` would
    # make the rolling burn-rate window miss freshly-injected sim rows.
    BD_TZ = timezone(timedelta(hours=6))
    t = datetime.now(BD_TZ).replace(tzinfo=None)

    result = build_alert_and_tickets(db, agent_id, t, providers=providers)

    if result["alert"] is None:
        return {
            "ok": True,
            "alert_id": None,
            "tickets": [],
            "message": "No alert-worthy conditions detected.",
        }

    alert_d = result["alert"]

    # Map lowercase severity string → SeverityLevel enum. The engine emits
    # "low"/"medium"/"high" (no "critical"), so we map "high" → HIGH and
    # "critical" would only arrive via direct callers (not produced here).
    sev_map = {"low": SeverityLevel.LOW, "medium": SeverityLevel.MEDIUM, "high": SeverityLevel.HIGH}
    severity_enum = sev_map.get(str(alert_d["severity"]).lower(), SeverityLevel.MEDIUM)

    alert_row = Alert(
        alert_id=alert_d["alert_id"],
        agent_id=alert_d["agent_id"],
        provider_id=alert_d.get("provider_id"),
        alert_type=AlertType(alert_d["alert_type"]),
        severity=severity_enum,
        message_bn=alert_d.get("message_bn") or "",
        confidence_score=alert_d["confidence_score"],
        master_evidence_json=alert_d["master_evidence_json"],
        created_at=alert_d["created_at"],
    )
    db.add(alert_row)

    ticket_ids: List[Dict[str, str]] = []
    for tk in result["tickets"]:
        ticket_row = Ticket(
            ticket_id=tk["ticket_id"],
            alert_id=tk["alert_id"],
            provider_id=tk["provider_id"],
            assigned_officer_id=tk["assigned_officer_id"] or "",
            current_owner_role=OwnerRole(tk["current_owner_role"]),
            status=TicketStatus(tk["status"]),
            evidence_json=tk["evidence_json"],
            created_at=tk["created_at"],
            updated_at=tk["updated_at"],
        )
        db.add(ticket_row)

        # Initial audit row. The AuditAction enum doesn't have a CREATED
        # value yet, so we use ACKNOWLEDGE — the notes_text makes the
        # creation intent explicit. A future migration can add a CREATED
        # enum value without breaking existing rows (we'd backfill once).
        audit_row = AuditLog(
            log_id=f"audit_{tk['ticket_id']}_created",
            ticket_id=tk["ticket_id"],
            action_taken=AuditAction.ACKNOWLEDGE,
            performed_by_role="SYSTEM",
            notes_text=(
                f"CREATED by analytics engine ({ctx.role}={ctx.user_id}); "
                f"alert_type={alert_d['alert_type']}, severity={alert_d['severity']}"
            ),
            timestamp=tk["created_at"],
        )
        db.add(audit_row)
        ticket_ids.append({
            "ticket_id": tk["ticket_id"],
            "provider_id": tk["provider_id"],
        })

    db.commit()

    return {
        "ok": True,
        "alert_id": alert_d["alert_id"],
        "alert_type": alert_d["alert_type"],
        "severity": alert_d["severity"],
        "tickets": ticket_ids,
    }