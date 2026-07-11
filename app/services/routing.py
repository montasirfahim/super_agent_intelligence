"""Officer routing — look up the territory office assigned to an (agent, provider).

Single point of truth used by `analytics_engine.build_ticket` so the engine
stays free of direct ORM routing logic.
"""
from __future__ import annotations

from sqlmodel import Session, select

from app.models import AgentProviderAssignment


def route_to_officer(db: Session, agent_id: str, provider_id: str) -> str:
    """Return the territory-office id responsible for this (agent, provider).

    Falls back to the first office for that provider if no specific
    assignment exists, so the ticket always has a valid owner.
    """
    assignment = db.exec(
        select(AgentProviderAssignment)
        .where(AgentProviderAssignment.agent_id == agent_id)
        .where(AgentProviderAssignment.provider_id == provider_id)
    ).first()
    if assignment and assignment.to_officer_id:
        return assignment.to_officer_id

    # Fallback: any office for this provider
    from app.models import TerritoryOffice
    fallback = db.exec(
        select(TerritoryOffice)
        .where(TerritoryOffice.provider_id == provider_id)
        .limit(1)
    ).first()
    return fallback.id if fallback else ""