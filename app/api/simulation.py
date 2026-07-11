"""Live transaction injection endpoint — used by the agent dashboard's
5-second poll cycle to make the physical-cash simulation feel alive.

POST /api/simulate/inject-transaction
Body: {agent_id: str, provider_id: str, type: 'CASH_OUT'|'CASH_IN', amount: number}

Inserts a brand-new TransactionStream row with timestamp=now, tx_day='Eid-eve',
and a unique tx_id like 'live_<wallet>_<nanosecond>'. The agent dashboard
picks it up on its next /api/dashboard/agent poll, dedups by tx_id, and
deducts/adds to the client-side live-cash ledger.

This is intentionally minimal — production would use a queue/WebSocket
push instead of poll-then-inject.
"""
from __future__ import annotations

import hashlib
import random
from datetime import datetime, timezone, timedelta
from decimal import Decimal

from fastapi import APIRouter, Body, HTTPException
from sqlmodel import Session, select

from app.database import engine
from app.models import (
    Agent,
    ProviderWallet,
    TransactionStream,
    TransactionType,
)

router = APIRouter(tags=["simulation"])

BD_TZ = timezone(timedelta(hours=6))


@router.post("/api/simulate/inject-transaction")
def inject_transaction(payload: dict = Body(default={})):
    """Append one new transaction to the live stream for an agent."""
    agent_id = (payload or {}).get("agent_id")
    provider_id = (payload or {}).get("provider_id")
    tx_type = (payload or {}).get("type")
    amount_raw = (payload or {}).get("amount")

    if not agent_id:
        raise HTTPException(400, "agent_id required")
    if provider_id not in ("1", "2", "3"):
        raise HTTPException(400, "provider_id must be '1' (bKash), '2' (Nagad), or '3' (Rocket)")
    if tx_type not in ("CASH_OUT", "CASH_IN"):
        raise HTTPException(400, "type must be CASH_OUT or CASH_IN")
    try:
        amount = float(amount_raw)
        if amount <= 0:
            raise ValueError
    except (TypeError, ValueError):
        raise HTTPException(400, "amount must be a positive number")

    # Customer hash — stable random per (provider, tx_id seed)
    seed = f"{provider_id}_{datetime.now().timestamp()}".encode()
    cust_hash = "cust_" + hashlib.sha256(seed).hexdigest()[:14]

    # Unique tx_id (avoid PK collision with the 30-minute demo stream)
    now = datetime.now(BD_TZ).replace(tzinfo=None)
    nonce = hashlib.md5(seed).hexdigest()[:8]
    tx_id = f"simlive_{agent_id}_{provider_id}_{int(now.timestamp())}_{nonce}"

    with Session(engine) as s:
        # Sanity: agent must exist
        if not s.get(Agent, agent_id):
            raise HTTPException(404, f"agent {agent_id} not found")
        # Wallet should exist for this agent+provider (created by the seeder)
        wallet = s.exec(
            select(ProviderWallet)
            .where(ProviderWallet.agent_id == agent_id)
            .where(ProviderWallet.provider_id == provider_id)
        ).first()
        if not wallet:
            raise HTTPException(404, f"no wallet for agent {agent_id} on provider {provider_id}")

        # Insert the new live transaction
        txn = TransactionStream(
            tx_id=tx_id,
            agent_id=agent_id,
            provider_id=provider_id,
            customer_id_hash=cust_hash,
            tx_type=TransactionType(tx_type),
            tx_day="Eid-eve",                # live stream tag
            amount=Decimal(amount),
            timestamp=now,
        )
        s.add(txn)
        # Don't mutate the wallet balance itself — the simulation is client-side
        # (the dashboard derives live physical-cash from the JSON starting value
        # minus the cumulative txns). DB wallet stays at its seeded value.
        s.commit()

    return {
        "ok": True,
        "tx_id": tx_id,
        "agent_id": agent_id,
        "provider_id": provider_id,
        "type": tx_type,
        "amount": amount,
        "timestamp": now.isoformat(),
    }


@router.post("/api/simulate/inject-burst")
def inject_burst(payload: dict = Body(default={})):
    """Convenience: inject N random live transactions for one agent in one shot.
    Used by demo scripts to make the dashboard visibly drain in seconds."""
    agent_id = (payload or {}).get("agent_id")
    n = int((payload or {}).get("count", 5))
    if not agent_id:
        raise HTTPException(400, "agent_id required")
    if n < 1 or n > 50:
        raise HTTPException(400, "count must be 1..50")

    inserted = []
    providers = ["1", "2", "3"]
    rng = random.Random()
    for _ in range(n):
        prov = rng.choice(providers)
        # 70% CASH_OUT, 30% CASH_IN — biases toward draining the drawer
        tx_type = "CASH_OUT" if rng.random() < 0.7 else "CASH_IN"
        amount = rng.randint(2_000, 18_000) if tx_type == "CASH_OUT" else rng.randint(1_000, 12_000)
        result = inject_transaction({"agent_id": agent_id, "provider_id": prov,
                                     "type": tx_type, "amount": amount})
        inserted.append(result)
    return {"ok": True, "inserted": inserted, "count": len(inserted)}