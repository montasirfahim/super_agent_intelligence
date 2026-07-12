"""Role-aware dashboard data endpoints.

Each endpoint enforces multi-tenant isolation:
- Agent sees only their own data (1 physical + their assigned wallets).
- Territory Officer sees tickets where assigned_officer_id matches their office.
  Only data for the provider tied to their office (e.g., bKash officer → bKash tickets).
- Risk Analyst sees tickets escalated from territory offices under their analyst_id.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from sqlalchemy.orm import selectinload
from sqlmodel import Session, select

from app.core.session import require_role
from app.database import get_db
from app.models import (
    Agent,
    AgentProviderAssignment,
    Alert,
    AlertType,
    AuditAction,
    AuditLog,
    Division,
    Provider,
    ProviderWallet,
    RiskAnalyst,
    SeverityLevel,
    TerritoryOffice,
    Ticket,
    TicketStatus,
    TransactionStream,
    TransactionType,
)


router = APIRouter(tags=["dashboard"])


BD_TZ = timezone(timedelta(hours=6))


def now_naive() -> datetime:
    """Return current BD time as a naive datetime (matches DB column type)."""
    return datetime.now(BD_TZ).replace(tzinfo=None)


# ---------------- Helpers ----------------

PROVIDER_NAME = {"1": "bKash", "2": "Nagad", "3": "Rocket"}


def fmt_bdt(value: Decimal | float | int | None) -> str:
    if value is None:
        return "৳0"
    n = int(value)
    s = f"{n:,}"
    return f"৳{s}"


def velocity_bdt_per_min(txns: list[TransactionStream], minutes: int = 30) -> tuple[float, float]:
    """Return (outflow_velocity, inflow_velocity) in BDT/min over the window."""
    cutoff = now_naive() - timedelta(minutes=minutes)
    out_v = in_v = 0.0
    out_n = in_n = 0
    for t in txns:
        if t.timestamp is None or t.timestamp < cutoff:
            continue
        if t.tx_type == TransactionType.CASH_OUT:
            out_v += float(t.amount)
            out_n += 1
        else:
            in_v += float(t.amount)
            in_n += 1
    window = max(minutes, 1)
    return out_v / window, in_v / window


def compute_t_runaway(balance: Decimal, outflow: float, inflow: float) -> dict[str, Any]:
    net = outflow - inflow
    if net <= 0 or balance <= 0:
        return {"t_minutes": None, "depleting": False, "net_velocity": round(net, 2)}
    t = float(balance) / net
    return {"t_minutes": round(t, 1), "depleting": True, "net_velocity": round(net, 2)}


def severity_from_runway(t: float | None) -> str:
    if t is None or t > 360:
        return "LOW"
    if t <= 30:
        return "CRITICAL"
    if t <= 60:
        return "HIGH"
    if t <= 180:
        return "MEDIUM"
    return "LOW"


def status_badge(status: str) -> str:
    palette = {
        "OPEN": "#ef4444",
        "ACKNOWLEDGED": "#f59e0b",
        "UNDER_REVIEW": "#3b82f6",
        "RESOLVED": "#10b981",
    }
    return palette.get(status, "#6b7280")


def severity_badge(sev: str) -> str:
    palette = {
        "LOW": "#10b981",
        "MEDIUM": "#f59e0b",
        "HIGH": "#ef4444",
        "CRITICAL": "#7c3aed",
    }
    return palette.get(sev, "#6b7280")


# ---------------- Role data resolvers ----------------

def resolve_agent_view(db: Session, agent_id: str) -> dict[str, Any]:
    agent = db.get(Agent, agent_id)
    if not agent:
        raise HTTPException(404, f"Agent {agent_id} not found")

    # ----------------------------------------------------------------
    # Authoritative draining totals — must run BEFORE wallet-card build
    # so each wallet card can carry its own Σ since-sim-start totals.
    # Computed from ALL `simlive_` txns (not just the last 15), which
    # fixes the previous bug where the client-side ledger silently
    # truncated its running totals to whatever fit in the last-15 view.
    # ----------------------------------------------------------------
    sim_txns = db.exec(
        select(TransactionStream)
        .where(TransactionStream.agent_id == agent_id)
        .where(TransactionStream.tx_id.like("simlive_%"))
    ).all()

    overall_cash_in = 0.0
    overall_cash_out = 0.0
    overall_count = 0
    by_provider: dict[str, dict[str, float]] = {
        "1": {"cash_in": 0.0, "cash_out": 0.0, "tx_count": 0},
        "2": {"cash_in": 0.0, "cash_out": 0.0, "tx_count": 0},
        "3": {"cash_in": 0.0, "cash_out": 0.0, "tx_count": 0},
    }
    for t in sim_txns:
        amt = float(t.amount)
        pid = t.provider_id
        if pid not in by_provider:
            continue
        if t.tx_type == TransactionType.CASH_OUT:
            overall_cash_out += amt
            by_provider[pid]["cash_out"] += amt
        else:
            overall_cash_in += amt
            by_provider[pid]["cash_in"] += amt
        by_provider[pid]["tx_count"] += 1
        overall_count += 1

    overall_net = overall_cash_in - overall_cash_out
    by_provider_net = {
        pid: {"net": v["cash_in"] - v["cash_out"], **v}
        for pid, v in by_provider.items()
    }

    # Wallets for this agent (one per provider they're assigned to)
    wallets = db.exec(
        select(ProviderWallet).where(ProviderWallet.agent_id == agent_id)
    ).all()
    wallet_by_provider = {w.provider_id: w for w in wallets}

    # Ensure all 3 provider slots appear, even if missing
    all_providers = [
        {"id": "1", "name": "bKash"},
        {"id": "2", "name": "Nagad"},
        {"id": "3", "name": "Rocket"},
    ]
    wallet_cards = []
    for p in all_providers:
        w = wallet_by_provider.get(p["id"])
        prov_totals = by_provider_net.get(p["id"], {"cash_in": 0.0, "cash_out": 0.0, "net": 0.0, "tx_count": 0})
        if w is None:
            wallet_cards.append({
                "provider_id": p["id"],
                "provider_name": p["name"],
                "balance": 0,
                "balance_fmt": fmt_bdt(0),
                "last_sync_time": "—",
                "has_wallet": False,
                # Server-authoritative since-sim-start totals, surfaced
                # even on a missing-wallet card so the dashboard always
                # shows what's draining.
                "cash_in_total":  round(prov_totals["cash_in"], 2),
                "cash_out_total": round(prov_totals["cash_out"], 2),
                "net_total":      round(prov_totals["net"], 2),
                "tx_count_total": int(prov_totals["tx_count"]),
                "cash_in_fmt":    fmt_bdt(prov_totals["cash_in"]),
                "cash_out_fmt":   fmt_bdt(prov_totals["cash_out"]),
                "net_fmt":        fmt_bdt(abs(prov_totals["net"])),
                "direction":      "DRAINING" if prov_totals["net"] < 0 else "STABLE",
            })
            continue

        # Velocity from recent transactions on this wallet
        cutoff = now_naive() - timedelta(minutes=30)
        from sqlalchemy import or_, not_ as _not
        txns = db.exec(
            select(TransactionStream)
            .where(TransactionStream.agent_id == agent_id)
            .where(TransactionStream.provider_id == p["id"])
            .where(TransactionStream.timestamp >= cutoff)
            .where(
                _not(or_(*[TransactionStream.tx_id.like(pfx + "%") for pfx in ("live_wallet_",)]))
            )
        ).all()
        out_v, in_v = velocity_bdt_per_min(txns, 30)
        forecast = compute_t_runaway(w.e_money_balance, out_v, in_v)

        wallet_cards.append({
            "provider_id": p["id"],
            "provider_name": p["name"],
            "balance": float(w.e_money_balance),
            "balance_fmt": fmt_bdt(w.e_money_balance),
            "last_sync_time": w.last_sync_time.strftime("%H:%M:%S"),
            "has_wallet": True,
            "outflow_velocity": round(out_v, 1),
            "inflow_velocity": round(in_v, 1),
            "t_runaway": forecast["t_minutes"],
            "t_runaway_fmt": "Stable" if not forecast["depleting"] else f"{int(forecast['t_minutes'])} min",
            "severity": severity_from_runway(forecast["t_minutes"]),
            # Server-authoritative since-sim-start totals — fed into the
            # wallet card so the deductions/additions lines match the
            # real running sum, not just the last-15 sample.
            "cash_in_total":  round(prov_totals["cash_in"], 2),
            "cash_out_total": round(prov_totals["cash_out"], 2),
            "net_total":      round(prov_totals["net"], 2),
            "tx_count_total": int(prov_totals["tx_count"]),
            "cash_in_fmt":    fmt_bdt(prov_totals["cash_in"]),
            "cash_out_fmt":   fmt_bdt(prov_totals["cash_out"]),
            "net_fmt":        fmt_bdt(abs(prov_totals["net"])),
            "direction":      "DRAINING" if prov_totals["net"] < 0 else "STABLE",
        })

    # Physical cash forecast (last 30 min velocity window)
    cutoff = now_naive() - timedelta(minutes=30)
    all_txns = db.exec(
        select(TransactionStream)
        .where(TransactionStream.agent_id == agent_id)
        .where(TransactionStream.timestamp >= cutoff)
    ).all()
    out_v, in_v = velocity_bdt_per_min(all_txns, 30)
    physical_forecast = compute_t_runaway(agent.shared_physical_cash, out_v, in_v)

    # Recent transactions (last 15) — for the live table view only.
    # Totals above are computed from sim_txns (all of them), not from
    # this list. Exclude the legacy `live_wallet_*` synthetic prefix so
    # stale rows from a removed seed path stop showing up here until
    # the next reset wipes them.
    from sqlalchemy import or_, not_
    recent_txns = db.exec(
        select(TransactionStream)
        .where(TransactionStream.agent_id == agent_id)
        .where(
            not_(or_(*[TransactionStream.tx_id.like(p + "%") for p in ("live_wallet_",)]))
        )
        .order_by(TransactionStream.timestamp.desc())
        .limit(15)
    ).all()

    # Active alerts for this agent
    alerts = db.exec(
        select(Alert)
        .where(Alert.agent_id == agent_id)
        .order_by(Alert.created_at.desc())
        .limit(5)
    ).all()

    return {
        "agent": {
            "agent_id": agent.agent_id,
            "shop_name": agent.shop_name,
            "area": agent.area,
            "district": agent.district,
            "shared_physical_cash": float(agent.shared_physical_cash),
            "shared_physical_cash_fmt": fmt_bdt(agent.shared_physical_cash),
            "status": agent.status.value if hasattr(agent.status, "value") else str(agent.status),
        },
        "physical": {
            **physical_forecast,
            "balance": float(agent.shared_physical_cash),
            "balance_fmt": fmt_bdt(agent.shared_physical_cash),
            "outflow_velocity": round(out_v, 1),
            "inflow_velocity": round(in_v, 1),
            "t_runaway_fmt": "Stable" if not physical_forecast["depleting"] else f"{int(physical_forecast['t_minutes'])} min",
            "severity": severity_from_runway(physical_forecast["t_minutes"]),
            # Server-authoritative since-sim-start totals.
            "cash_in_total":  round(overall_cash_in, 2),
            "cash_out_total": round(overall_cash_out, 2),
            "net_total":      round(overall_net, 2),
            "tx_count_total": overall_count,
            "cash_in_fmt":    fmt_bdt(overall_cash_in),
            "cash_out_fmt":   fmt_bdt(overall_cash_out),
            "net_fmt":        fmt_bdt(abs(overall_net)),
            "direction":      "DRAINING" if overall_net < 0 else "STABLE",
        },
        "wallets": wallet_cards,
        # Authoritative draining totals — across every sim txn for this
        # agent, not just the last 15 in `recent_transactions`. Used by
        # the dashboard to show how much has actually drained, in BDT,
        # since the sim started. The starting balance is implicit:
        # starting = current_balance + (cash_out − cash_in).
        "draining_totals": {
            "overall": {
                "cash_in":    round(overall_cash_in, 2),
                "cash_out":   round(overall_cash_out, 2),
                "net":        round(overall_net, 2),
                "tx_count":   overall_count,
                "cash_in_fmt":  fmt_bdt(overall_cash_in),
                "cash_out_fmt": fmt_bdt(overall_cash_out),
                "net_fmt":      fmt_bdt(abs(overall_net)),
                "direction":     "DRAINING" if overall_net < 0 else "STABLE",
            },
            "by_provider": {
                pid: {
                    "cash_in":     round(v["cash_in"], 2),
                    "cash_out":    round(v["cash_out"], 2),
                    "net":         round(v["net"], 2),
                    "tx_count":    int(v["tx_count"]),
                    "cash_in_fmt":  fmt_bdt(v["cash_in"]),
                    "cash_out_fmt": fmt_bdt(v["cash_out"]),
                    "net_fmt":      fmt_bdt(abs(v["net"])),
                    "direction":     "DRAINING" if v["net"] < 0 else "STABLE",
                }
                for pid, v in by_provider_net.items()
            },
        },
        "recent_transactions": [
            {
                "tx_id": t.tx_id,
                "provider_id": t.provider_id,                # numeric '1'/'2'/'3' for client-side ledger routing
                "provider": PROVIDER_NAME.get(t.provider_id, t.provider_id),
                "type": t.tx_type.value if hasattr(t.tx_type, "value") else str(t.tx_type),
                "amount_fmt": fmt_bdt(t.amount),
                "amount": float(t.amount),
                "customer_hash_short": t.customer_id_hash[:14] + "…",
                "timestamp": t.timestamp.strftime("%H:%M:%S"),
            }
            for t in recent_txns
        ],
        "alerts": [
            {
                "alert_id": a.alert_id,
                "alert_type": a.alert_type.value if hasattr(a.alert_type, "value") else str(a.alert_type),
                "severity": a.severity.value if hasattr(a.severity, "value") else str(a.severity),
                "message_bn": a.message_bn,
                "confidence_score": float(a.confidence_score),
                "created_at": a.created_at.strftime("%Y-%m-%d %H:%M"),
            }
            for a in alerts
        ],
    }


def resolve_officer_view(db: Session, office_id: str) -> dict[str, Any]:
    office = db.get(TerritoryOffice, office_id)
    if not office:
        raise HTTPException(404, f"Office {office_id} not found")

    # Agents assigned to this office (for this office's provider only)
    assignments = db.exec(
        select(AgentProviderAssignment)
        .where(AgentProviderAssignment.to_officer_id == office_id)
    ).all()
    agent_ids = [a.agent_id for a in assignments]

    # Tickets for this office (strict provider boundary)
    tickets = db.exec(
        select(Ticket, Alert)
        .join(Alert, Ticket.alert_id == Alert.alert_id)
        .where(Ticket.assigned_officer_id == office_id)
        .where(Ticket.provider_id == office.provider_id)
        .order_by(Ticket.created_at.desc())
    ).all()

    open_count = sum(1 for t, _ in tickets if t.status != TicketStatus.RESOLVED)
    resolved_count = sum(1 for t, _ in tickets if t.status == TicketStatus.RESOLVED)

    # MTTA / MTTR from audit logs
    mtta = mttr = None
    audit_logs = db.exec(
        select(AuditLog)
        .join(Ticket, AuditLog.ticket_id == Ticket.ticket_id)
        .where(Ticket.assigned_officer_id == office_id)
    ).all()
    if audit_logs:
        ack_times = []
        resolve_times = []
        for log in audit_logs:
            if log.action_taken == AuditAction.ACKNOWLEDGE:
                ack_times.append(log.timestamp)
        # Approximation: avg minutes from ticket creation to first acknowledge
        ticket_map = {t.ticket_id: t for t, _ in tickets}
        if ack_times:
            deltas = []
            for log in audit_logs:
                if log.action_taken == AuditAction.ACKNOWLEDGE and log.ticket_id in ticket_map:
                    delta = (log.timestamp - ticket_map[log.ticket_id].created_at).total_seconds() / 60
                    if delta > 0:
                        deltas.append(delta)
            if deltas:
                mtta = round(sum(deltas) / len(deltas), 1)

    ticket_list = []
    for t, a in tickets:
        ticket_list.append({
            "ticket_id": t.ticket_id,
            "alert_id": t.alert_id,
            "agent_id": a.agent_id,
            "status": t.status.value if hasattr(t.status, "value") else str(t.status),
            "severity": a.severity.value if hasattr(a.severity, "value") else str(a.severity),
            "alert_type": a.alert_type.value if hasattr(a.alert_type, "value") else str(a.alert_type),
            "message_bn": a.message_bn,
            "created_at": t.created_at.strftime("%Y-%m-%d %H:%M"),
            "updated_at": t.updated_at.strftime("%Y-%m-%d %H:%M"),
            "evidence": json.loads(t.evidence_json) if t.evidence_json else {},
            "confidence_score": float(a.confidence_score),
        })

    return {
        "office": {
            "id": office.id,
            "name": office.name,
            "provider": PROVIDER_NAME.get(office.provider_id, office.provider_id),
            "area_name": office.area_name,
        },
        "agent_count": len(agent_ids),
        "tickets": ticket_list,
        "open_count": open_count,
        "resolved_count": resolved_count,
        "mtta_minutes": mtta,
        "mttr_minutes": mttr,
    }


def resolve_analyst_view(db: Session, analyst_id: str) -> dict[str, Any]:
    analyst = db.get(RiskAnalyst, analyst_id)
    if not analyst:
        raise HTTPException(404, f"Analyst {analyst_id} not found")

    # Territory offices under this analyst
    offices = db.exec(
        select(TerritoryOffice)
        .where(TerritoryOffice.risk_analyst_id == analyst_id)
    ).all()
    office_ids = [o.id for o in offices]

    # Tickets escalated from those offices
    tickets = []
    if office_ids:
        tickets = db.exec(
            select(Ticket, Alert, Agent)
            .join(Alert, Ticket.alert_id == Alert.alert_id)
            .join(Agent, Alert.agent_id == Agent.agent_id)
            .where(Ticket.assigned_officer_id.in_(office_ids))
            .order_by(Ticket.updated_at.desc())
        ).all()

    # Group by status for funnel
    funnel = {"OPEN": 0, "ACKNOWLEDGED": 0, "UNDER_REVIEW": 0, "RESOLVED": 0}
    ticket_list = []
    for t, a, agent in tickets:
        st = t.status.value if hasattr(t.status, "value") else str(t.status)
        funnel[st] = funnel.get(st, 0) + 1
        ticket_list.append({
            "ticket_id": t.ticket_id,
            "alert_id": t.alert_id,
            "agent_id": a.agent_id,
            "shop_name": agent.shop_name,
            "area": agent.area,
            "status": st,
            "severity": a.severity.value if hasattr(a.severity, "value") else str(a.severity),
            "alert_type": a.alert_type.value if hasattr(a.alert_type, "value") else str(a.alert_type),
            "message_bn": a.message_bn,
            "created_at": t.created_at.strftime("%Y-%m-%d %H:%M"),
            "updated_at": t.updated_at.strftime("%Y-%m-%d %H:%M"),
            "evidence": json.loads(t.evidence_json) if t.evidence_json else {},
            "confidence_score": float(a.confidence_score),
            "provider": PROVIDER_NAME.get(t.provider_id, t.provider_id),
        })

    # Average confidence across escalated tickets
    confidences = [t["confidence_score"] for t in ticket_list if t.get("confidence_score") is not None]
    avg_confidence = round(sum(confidences) / len(confidences), 4) if confidences else 0.0

    return {
        "analyst": {
            "id": analyst.id,
            "name": analyst.name,
            "provider": PROVIDER_NAME.get(analyst.provider_id, analyst.provider_id),
            "area_name": analyst.area_name,
            "division": analyst.div_id,
        },
        "office_count": len(office_ids),
        "offices": [{"id": o.id, "name": o.name, "area": o.area_name} for o in offices],
        "tickets": ticket_list,
        "funnel": funnel,
        "avg_confidence": avg_confidence,
        "total_tickets": len(ticket_list),
    }


# ---------------- Endpoints ----------------

@router.get("/api/dashboard/agent")
def agent_dashboard(
    request: Request,
    db: Session = Depends(get_db),
):
    ctx = require_role(request, "agent")
    if not ctx.user_id:
        raise HTTPException(400, "user_id query param required for agent role")
    return resolve_agent_view(db, ctx.user_id)


@router.get("/api/dashboard/officer")
def officer_dashboard(
    request: Request,
    db: Session = Depends(get_db),
):
    ctx = require_role(request, "officer")
    if not ctx.user_id:
        raise HTTPException(400, "user_id query param required for officer role")
    return resolve_officer_view(db, ctx.user_id)


@router.get("/api/dashboard/analyst")
def analyst_dashboard(
    request: Request,
    db: Session = Depends(get_db),
):
    ctx = require_role(request, "analyst")
    if not ctx.user_id:
        raise HTTPException(400, "user_id query param required for analyst role")
    return resolve_analyst_view(db, ctx.user_id)


@router.get("/api/tickets/{ticket_id}/evidence")
def get_ticket_evidence(
    ticket_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Return the full evidence payload for a ticket, respecting role boundaries."""
    ctx = require_role(request, "agent", "officer", "analyst")
    ticket = db.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(404, "Ticket not found")

    alert = db.get(Alert, ticket.alert_id)
    if not alert:
        raise HTTPException(404, "Alert not found")

    # Tenant boundary checks
    if ctx.role == "agent":
        if alert.agent_id != ctx.user_id:
            raise HTTPException(403, "Ticket not for this agent")
    elif ctx.role == "officer":
        if ticket.assigned_officer_id != ctx.user_id:
            raise HTTPException(403, "Ticket not assigned to your office")
    elif ctx.role == "analyst":
        office = db.get(TerritoryOffice, ticket.assigned_officer_id)
        if not office or office.risk_analyst_id != ctx.user_id:
            raise HTTPException(403, "Ticket not under your analyst group")

    evidence = json.loads(ticket.evidence_json) if ticket.evidence_json else {}
    return {
        "ticket_id": ticket.ticket_id,
        "alert_id": ticket.alert_id,
        "agent_id": alert.agent_id,
        "provider_id": ticket.provider_id,
        "status": ticket.status.value if hasattr(ticket.status, "value") else str(ticket.status),
        "alert_type": alert.alert_type.value if hasattr(alert.alert_type, "value") else str(alert.alert_type),
        "severity": alert.severity.value if hasattr(alert.severity, "value") else str(alert.severity),
        "confidence_score": float(alert.confidence_score),
        "message_bn": alert.message_bn,
        "evidence": evidence,
    }


# ---------------- Ticket actions ----------------

@router.post("/api/tickets/{ticket_id}/acknowledge")
def acknowledge_ticket(
    ticket_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Officer acknowledges a ticket: OPEN → ACKNOWLEDGED."""
    ctx = require_role(request, "officer", "analyst")
    ticket = db.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(404, "Ticket not found")

    # Tenant boundary
    if ctx.role == "officer" and ticket.assigned_officer_id != ctx.user_id:
        raise HTTPException(403, "Ticket not assigned to your office")

    if ticket.status != TicketStatus.OPEN:
        raise HTTPException(409, f"Ticket is {ticket.status.value}, cannot acknowledge")

    ticket.status = TicketStatus.ACKNOWLEDGED
    ticket.current_owner_role = "FIELD_OFFICER"
    ticket.updated_at = now_naive()
    db.add(AuditLog(
        log_id=f"audit_{ticket_id}_{int(datetime.now().timestamp())}_ack",
        ticket_id=ticket_id,
        action_taken=AuditAction.ACKNOWLEDGE,
        performed_by_role=ctx.role.upper(),
        notes_text=f"Acknowledged by {ctx.role}",
        timestamp=now_naive(),
    ))
    db.commit()
    return {"ok": True, "ticket_id": ticket_id, "new_status": "ACKNOWLEDGED"}


@router.post("/api/tickets/{ticket_id}/escalate")
def escalate_ticket(
    ticket_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Officer escalates a ticket to their risk analyst: ACKNOWLEDGED → UNDER_REVIEW."""
    ctx = require_role(request, "officer")
    ticket = db.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(404, "Ticket not found")

    if ticket.assigned_officer_id != ctx.user_id:
        raise HTTPException(403, "Ticket not assigned to your office")

    if ticket.status not in (TicketStatus.OPEN, TicketStatus.ACKNOWLEDGED):
        raise HTTPException(409, f"Ticket is {ticket.status.value}, cannot escalate")

    ticket.status = TicketStatus.UNDER_REVIEW
    ticket.current_owner_role = "AREA_MANAGER"
    ticket.updated_at = now_naive()
    db.add(AuditLog(
        log_id=f"audit_{ticket_id}_{int(datetime.now().timestamp())}_esc",
        ticket_id=ticket_id,
        action_taken=AuditAction.ESCALATE,
        performed_by_role="FIELD_OFFICER",
        notes_text="Escalated to risk analyst for evidence review",
        timestamp=now_naive(),
    ))
    db.commit()
    return {"ok": True, "ticket_id": ticket_id, "new_status": "UNDER_REVIEW"}


@router.post("/api/tickets/{ticket_id}/add-note")
def add_ticket_note(
    ticket_id: str,
    payload: dict = Body(default={}),
    request: Request = None,
    db: Session = Depends(get_db),
):
    """Add a field note to a ticket. Body: {"notes_text": "..."}."""
    ctx = require_role(request, "officer", "analyst")
    ticket = db.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(404, "Ticket not found")

    notes_text = (payload or {}).get("notes_text", "").strip()
    if not notes_text:
        raise HTTPException(400, "notes_text required in JSON body")

    db.add(AuditLog(
        log_id=f"audit_{ticket_id}_{int(datetime.now().timestamp())}_note",
        ticket_id=ticket_id,
        action_taken=AuditAction.ADD_NOTE,
        performed_by_role=ctx.role.upper(),
        notes_text=notes_text[:1000],
        timestamp=now_naive(),
    ))
    ticket.updated_at = now_naive()
    db.commit()
    return {"ok": True, "ticket_id": ticket_id}


@router.post("/api/tickets/{ticket_id}/confirm-anomaly")
def confirm_anomaly(
    ticket_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Risk analyst confirms anomaly is REAL: UNDER_REVIEW → RESOLVED, notes flag."""
    ctx = require_role(request, "analyst")
    ticket = db.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(404, "Ticket not found")

    if ticket.status != TicketStatus.UNDER_REVIEW:
        raise HTTPException(409, "Ticket must be UNDER_REVIEW before analyst can confirm")

    ticket.status = TicketStatus.RESOLVED
    ticket.current_owner_role = "CENTRAL_OPS"
    ticket.updated_at = now_naive()
    db.add(AuditLog(
        log_id=f"audit_{ticket_id}_{int(datetime.now().timestamp())}_real",
        ticket_id=ticket_id,
        action_taken=AuditAction.CLOSE,
        performed_by_role="RISK_ANALYST",
        notes_text="Anomaly confirmed as REAL_OPERATIONAL_PATTERN by analyst",
        timestamp=now_naive(),
    ))
    db.commit()
    return {"ok": True, "ticket_id": ticket_id, "new_status": "RESOLVED", "verdict": "REAL"}


@router.post("/api/tickets/{ticket_id}/mark-false-positive")
def mark_false_positive(
    ticket_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Risk analyst marks anomaly as FALSE_POSITIVE: UNDER_REVIEW → RESOLVED."""
    ctx = require_role(request, "analyst")
    ticket = db.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(404, "Ticket not found")

    if ticket.status != TicketStatus.UNDER_REVIEW:
        raise HTTPException(409, "Ticket must be UNDER_REVIEW before analyst can verdict")

    ticket.status = TicketStatus.RESOLVED
    ticket.current_owner_role = "AREA_MANAGER"
    ticket.updated_at = now_naive()
    db.add(AuditLog(
        log_id=f"audit_{ticket_id}_{int(datetime.now().timestamp())}_fp",
        ticket_id=ticket_id,
        action_taken=AuditAction.CLOSE,
        performed_by_role="RISK_ANALYST",
        notes_text="Marked as FALSE_POSITIVE — likely Eid/festival demand or data quality issue",
        timestamp=now_naive(),
    ))
    db.commit()
    return {"ok": True, "ticket_id": ticket_id, "new_status": "RESOLVED", "verdict": "FALSE_POSITIVE"}


# ---------------- Lookup endpoints (for user_id discovery) ----------------

@router.get("/api/lookup/agents")
def list_sample_agents(db: Session = Depends(get_db)):
    """Return a sample of agent IDs for demo login."""
    agents = db.exec(select(Agent).limit(20)).all()
    return [{"agent_id": a.agent_id, "shop_name": a.shop_name, "area": a.area} for a in agents]


@router.get("/api/lookup/offices")
def list_sample_offices(db: Session = Depends(get_db)):
    """Return a sample of office IDs for demo login."""
    offices = db.exec(select(TerritoryOffice).limit(20)).all()
    return [
        {
            "id": o.id,
            "name": o.name,
            "provider": PROVIDER_NAME.get(o.provider_id, o.provider_id),
            "area_name": o.area_name,
        }
        for o in offices
    ]


@router.get("/api/lookup/analysts")
def list_sample_analysts(db: Session = Depends(get_db)):
    """Return a sample of analyst IDs for demo login."""
    analysts = db.exec(select(RiskAnalyst).limit(20)).all()
    return [
        {
            "id": a.id,
            "name": a.name,
            "provider": PROVIDER_NAME.get(a.provider_id, a.provider_id),
            "division": a.div_id,
            "area_name": a.area_name,
        }
        for a in analysts
    ]
