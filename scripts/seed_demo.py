"""Deterministic demo data seeder for Super Agent Intelligence.

Run: python scripts/seed_demo.py
Idempotent: clears existing domain data before inserting.
"""
from __future__ import annotations

import hashlib
import json
import random
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

# Allow running as `python scripts/seed_demo.py` from project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session, SQLModel, delete, select

from app.database import engine, create_all_tables
from app.models import (
    Agent,
    AgentProviderAssignment,
    AgentStatus,
    Alert,
    AlertType,
    AuditAction,
    AuditLog,
    Division,
    OwnerRole,
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


SEED = 20260711
BD_TZ = timezone(timedelta(hours=6))


# ---------------- Reference data ----------------

DIVISIONS = [
    ("dhaka", "Dhaka"),
    ("chattogram", "Chattogram"),
    ("sylhet", "Sylhet"),
    ("khulna", "Khulna"),
    ("rajshahi", "Rajshahi"),
    ("barishal", "Barishal"),
    ("rangpur", "Rangpur"),
    ("mymensingh", "Mymensingh"),
]

AREAS_BY_DIV = {
    "sylhet": ["Zindabazar", "Bondor", "Akhalia", "Subhanighat"],
    "dhaka": ["Mirpur", "Uttara", "Dhanmondi", "Old Dhaka"],
    "chattogram": ["Agrabad", "Khulshi", "Patiya"],
    "khulna": ["Khalishpur", "Sonadanga"],
    "rajshahi": ["Shaheb Bazar", "Talaimari"],
    "barishal": ["Sadar Road"],
    "rangpur": ["Central", "Pairabandh"],
    "mymensingh": ["Ganginarpar", "Krishna Nagar"],
}

ANALYST_NAMES = [
    "Rahim Ahmed", "Karim Hossain", "Jamal Uddin", "Farhan Chowdhury",
    "Imran Khan", "Selim Reza", "Tariq Aziz", "Nadim Hassan",
    "Saiful Islam", "Mahmud Rahman", "Asif Iqbal", "Rashed Khan",
    "Noman Sheikh", "Babul Mia", "Shafiqul Islam", "Kamrul Hasan",
    "Jasim Uddin", "Mamunur Rashid", "Tuhin Ahmed", "Shahin Alam",
    "Ripon Mia", "Helal Uddin", "Anwar Hossain", "Badiuzzaman",
]

OFFICE_PREFIXES = ["North", "South", "East", "West", "Central"]


def hash_customer(prefix: str, idx: int) -> str:
    return f"cust_{hashlib.sha256(f'{prefix}_{idx}'.encode()).hexdigest()[:14]}"


def fmt_bdt(amount: Decimal | float | int) -> str:
    n = int(amount)
    s = str(n)
    if len(s) <= 3:
        return f"৳{s}"
    last3 = s[-3:]
    rest = s[:-3]
    groups = []
    while len(rest) > 2:
        groups.insert(0, rest[-2:])
        rest = rest[:-2]
    if rest:
        groups.insert(0, rest)
    return f"৳{','.join(groups)}.{last3}"


# ---------------- Seeder ----------------

def seed():
    create_all_tables()
    rng = random.Random(SEED)

    with Session(engine) as s:
        # Wipe all demo data first for idempotency
        s.exec(delete(AuditLog))
        s.exec(delete(Ticket))
        s.exec(delete(Alert))
        s.exec(delete(TransactionStream))
        s.exec(delete(ProviderWallet))
        s.exec(delete(AgentProviderAssignment))
        s.exec(delete(Agent))
        s.exec(delete(TerritoryOffice))
        s.exec(delete(RiskAnalyst))
        s.exec(delete(Division))
        s.exec(delete(Provider))
        s.commit()

        # 0. Providers (must come before any FK references)
        s.add_all([
            Provider(id="1", provider_name="bKash"),
            Provider(id="2", provider_name="Nagad"),
            Provider(id="3", provider_name="Rocket"),
        ])
        s.commit()

        # 1. Divisions
        div_records = [Division(div_id=did, div_name=name) for did, name in DIVISIONS]
        s.add_all(div_records)

        # 2. Risk analysts: 3 per division = 24
        analyst_records = []
        for did, dname in DIVISIONS:
            for prov in ("bkash", "nagad", "rocket"):
                name = rng.choice(ANALYST_NAMES)
                username = f"analyst.{prov[:3]}.{did[:3]}"
                analyst_records.append(
                    RiskAnalyst(
                        id=f"analyst_{prov}_{did}",
                        name=name,
                        provider_id={"bkash": "1", "nagad": "2", "rocket": "3"}[prov],
                        div_id=did,
                        area_name=rng.choice(AREAS_BY_DIV[did]),
                        username=username,
                        password="123456",
                    )
                )
        s.add_all(analyst_records)

        # 3. Territory offices: 2 per (provider, division) = 48
        office_records = []
        for did, dname in DIVISIONS:
            for prov in ("bkash", "nagad", "rocket"):
                prov_id = {"bkash": "1", "nagad": "2", "rocket": "3"}[prov]
                for i in range(2):
                    username = f"officer.{prov[:3]}.{did[:3]}.{i+1}"
                    office_records.append(
                        TerritoryOffice(
                            id=f"office_{prov}_{did}_{i+1}",
                            name=f"{rng.choice(OFFICE_PREFIXES)} {AREAS_BY_DIV[did][i % len(AREAS_BY_DIV[did])]} ({prov.title()})",
                            provider_id=prov_id,
                            div_id=did,
                            area_name=AREAS_BY_DIV[did][i % len(AREAS_BY_DIV[did])],
                            risk_analyst_id=f"analyst_{prov}_{did}",
                            username=username,
                            password="123456",
                        )
                    )
        s.add_all(office_records)
        s.commit()

        # 4. Agents: 6 per office = 288 agents (smaller for demo speed)
        agent_records = []
        assignment_records = []
        wallet_records = []
        for office in office_records:
            # Derive the division id from the office id (format: office_<prov>_<div>_<n>)
            parts = office.id.split("_")
            office_div = parts[2]
            for i in range(6):
                agent_id = f"agent_{parts[1]}_{parts[2]}_{parts[3]}_{i+1:02d}"
                username = f"agent.{parts[1][:3]}.{parts[2][:3]}.{parts[3]}.{i+1:02d}"
                # Realistic band per provider: bKash larger, Rocket smaller
                if office.provider_id == "1":
                    cash = rng.randint(100_000, 250_000)
                elif office.provider_id == "2":
                    cash = rng.randint(80_000, 180_000)
                else:
                    cash = rng.randint(60_000, 140_000)
                agent_records.append(
                    Agent(
                        agent_id=agent_id,
                        shop_name=f"{office.area_name} {rng.choice(['Store', 'Point', 'Center', 'Cash Point'])} #{rng.randint(1,999)}",
                        area=office.area_name,
                        district=office_div,
                        shared_physical_cash=Decimal(cash),
                        status=AgentStatus.ACTIVE,
                        username=username,
                        password="123456",
                    )
                )
                assignment_records.append(
                    AgentProviderAssignment(
                        agent_id=agent_id,
                        provider_id=office.provider_id,
                        to_officer_id=office.id,
                    )
                )
        s.add_all(agent_records)
        s.add_all(assignment_records)

        # 5. Wallets: per (agent, assigned provider) — realistic bands
        for asg in assignment_records:
            if asg.provider_id == "1":
                bal = rng.randint(150_000, 450_000)
            elif asg.provider_id == "2":
                bal = rng.randint(80_000, 250_000)
            else:
                bal = rng.randint(40_000, 150_000)
            wallet_records.append(
                ProviderWallet(
                    wallet_id=f"wallet_{asg.agent_id}_{asg.provider_id}",
                    agent_id=asg.agent_id,
                    provider_id=asg.provider_id,
                    e_money_balance=Decimal(bal),
                    last_sync_time=datetime.now(BD_TZ) - timedelta(minutes=rng.randint(1, 30)),
                )
            )
        s.add_all(wallet_records)
        s.commit()

        # 6. Recent transactions for each wallet — 6 hours of history
        tx_records = []
        now = datetime.now(BD_TZ)
        for wallet in wallet_records:
            for mins_ago in range(360, 0, -2):
                if rng.random() < 0.55:  # ~55% chance of a transaction every 2 min
                    ttype = rng.choices(
                        [TransactionType.CASH_IN, TransactionType.CASH_OUT],
                        weights=[0.45, 0.55],
                    )[0]
                    if ttype == TransactionType.CASH_OUT:
                        amount = rng.randint(500, 12_000)
                    else:
                        amount = rng.randint(200, 8_000)
                    tx_records.append(
                        TransactionStream(
                            tx_id=f"tx_{wallet.wallet_id}_{mins_ago}",
                            agent_id=wallet.agent_id,
                            provider_id=wallet.provider_id,
                            customer_id_hash=hash_customer(wallet.provider_id, rng.randint(1, 800)),
                            tx_type=ttype,
                            amount=Decimal(amount),
                            timestamp=now - timedelta(minutes=mins_ago),
                        )
                    )
        s.add_all(tx_records)
        s.commit()

        # 7. A handful of pre-seeded alerts + tickets + audit logs to demonstrate the dashboards
        demo_alerts = []
        demo_tickets = []
        demo_audit = []
        sample_assignments = assignment_records[:30]  # take first 30 assignments
        for idx, asg in enumerate(sample_assignments):
            agent_id = asg.agent_id
            provider_id = asg.provider_id
            officer_id = asg.to_officer_id

            alert_id = f"alert_demo_{idx:03d}"
            ticket_id = f"ticket_demo_{idx:03d}"

            alert_type = AlertType.LIQUIDITY_SHORTAGE if idx % 2 == 0 else AlertType.BEHAVIORAL_ANOMALY
            severity = [SeverityLevel.MEDIUM, SeverityLevel.HIGH, SeverityLevel.CRITICAL][idx % 3]
            confidence = Decimal(str(round(0.55 + rng.random() * 0.4, 4)))

            evidence = {
                "alert_id": alert_id,
                "agent_id": agent_id,
                "provider_id": provider_id,
                "detection_timestamp": now.isoformat(),
                "triggered_signals": (
                    ["velocity_spike", "account_clustering"] if alert_type == AlertType.BEHAVIORAL_ANOMALY
                    else ["first_to_deplete"]
                ),
                "signals_detail": [
                    {
                        "kind": "velocity_spike",
                        "z_score": round(2.6 + rng.random() * 1.5, 2),
                        "current_bdt_per_min": rng.randint(8000, 25000),
                        "baseline_bdt_per_min": rng.randint(2000, 6000),
                    }
                ] if alert_type == AlertType.BEHAVIORAL_ANOMALY else [
                    {
                        "kind": "first_to_deplete",
                        "resource": "bkash_e_money" if provider_id == "1" else ("nagad_e_money" if provider_id == "2" else "rocket_e_money"),
                        "t_runaway_minutes": rng.randint(15, 180),
                        "outflow_velocity": rng.randint(2000, 8000),
                        "inflow_velocity": rng.randint(500, 3000),
                    }
                ],
                "c_risk": float(confidence),
                "false_positive_caveats": [
                    "Eid-eve baseline may inflate expectations",
                    "Localized festival demand possible",
                ],
                "requires_human_review": True,
                "language_warning": "Unusual operational patterns requiring review",
            }

            message_bn = (
                "গত ১২ মিনিটে অস্বাভাবিক ক্যাশ-আউটের ধারা দেখা গেছে। লেনদেনগুলো পর্যালোচনা করা প্রয়োজন।"
                if alert_type == AlertType.BEHAVIORAL_ANOMALY
                else "নির্দিষ্ট প্রোভাইডারের ই-মানি দ্রুত শেষ হচ্ছে। অতিরিক্ত নগদ ব্যবস্থা করার পরামর্শ দেওয়া হচ্ছে।"
            )

            demo_alerts.append(
                Alert(
                    alert_id=alert_id,
                    agent_id=agent_id,
                    provider_id=provider_id,
                    alert_type=alert_type,
                    severity=severity,
                    message_bn=message_bn,
                    confidence_score=confidence,
                    master_evidence_json=json.dumps(evidence),
                    created_at=now - timedelta(minutes=rng.randint(5, 240)),
                )
            )

            # 70% OPEN, 20% ACKNOWLEDGED, 10% UNDER_REVIEW (none RESOLVED — keeps officer dashboard populated)
            status_roll = rng.random()
            if status_roll < 0.7:
                status = TicketStatus.OPEN
                owner = OwnerRole.FIELD_OFFICER
            elif status_roll < 0.9:
                status = TicketStatus.ACKNOWLEDGED
                owner = OwnerRole.FIELD_OFFICER
            else:
                status = TicketStatus.UNDER_REVIEW
                owner = OwnerRole.AREA_MANAGER

            ticket_evidence = {**evidence, "ticket_id": ticket_id}
            demo_tickets.append(
                Ticket(
                    ticket_id=ticket_id,
                    alert_id=alert_id,
                    provider_id=provider_id,
                    assigned_officer_id=officer_id,
                    current_owner_role=owner,
                    status=status,
                    evidence_json=json.dumps(ticket_evidence),
                    created_at=now - timedelta(minutes=rng.randint(5, 240)),
                    updated_at=now - timedelta(minutes=rng.randint(1, 30)),
                )
            )

            demo_audit.append(
                AuditLog(
                    log_id=f"audit_demo_{idx:03d}_create",
                    ticket_id=ticket_id,
                    action_taken=AuditAction.ACKNOWLEDGE if status != TicketStatus.OPEN else AuditAction.ADD_NOTE,
                    performed_by_role="SYSTEM",
                    notes_text="Alert auto-routed from analytics engine",
                    timestamp=now - timedelta(minutes=rng.randint(5, 240)),
                )
            )

        s.add_all(demo_alerts)
        s.add_all(demo_tickets)
        s.add_all(demo_audit)
        s.commit()

        # Summary
        print(f"[seed] OK")
        print(f"  Divisions:        {len(s.exec(select(Division)).all())}")
        print(f"  Risk Analysts:    {len(s.exec(select(RiskAnalyst)).all())}")
        print(f"  Territory Offices:{len(s.exec(select(TerritoryOffice)).all())}")
        print(f"  Agents:           {len(s.exec(select(Agent)).all())}")
        print(f"  Assignments:      {len(s.exec(select(AgentProviderAssignment)).all())}")
        print(f"  Wallets:          {len(s.exec(select(ProviderWallet)).all())}")
        print(f"  Transactions:     {len(s.exec(select(TransactionStream)).all())}")
        print(f"  Alerts:           {len(s.exec(select(Alert)).all())}")
        print(f"  Tickets:          {len(s.exec(select(Ticket)).all())}")
        print(f"  Audit logs:       {len(s.exec(select(AuditLog)).all())}")

        print("\n[seed] Demo credentials (all passwords are '123456'):")
        # First analyst, first officer, first agent from each provider
        sample_analyst = s.exec(select(RiskAnalyst).limit(1)).first()
        sample_officer = s.exec(select(TerritoryOffice).limit(1)).first()
        sample_agents = s.exec(select(Agent).limit(3)).all()
        if sample_analyst:
            print(f"  Risk Analyst:    username={sample_analyst.username}  password=123456")
        if sample_officer:
            print(f"  Territory Office:username={sample_officer.username}  password=123456")
        for sa in sample_agents:
            print(f"  Agent:           username={sa.username}  password=123456")


if __name__ == "__main__":
    seed()