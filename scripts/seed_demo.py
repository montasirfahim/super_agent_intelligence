"""Deterministic demo data seeder for Super Agent Intelligence.

Reads base_dataset.json as the canonical reference (the same JSON the login
endpoint authenticates against), then mirrors it into Postgres so that:

  - Agent.agent_id    ==  base_dataset.agent[i].agent_id   (e.g. "agent1", "agent1000")
  - TerritoryOffice.id == base_dataset.territory-officer[i].id
  - RiskAnalyst.id    == base_dataset.risk-analyst[i].id

After loading the reference rows it synthesizes:
  - E-money wallets per (agent, assigned-provider)
  - Live "Eid-eve" transactions (last 30 min) tagged tx_day='Eid-eve'
  - A handful of demo alerts/tickets/audit logs for the dashboards

Run:  python scripts/seed_demo.py
Idempotent: wipes existing domain data before inserting.
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

# Map JSON's "bkash"/"nagad"/"rocket" provider ids to DB numeric ids (1/2/3)
JSON_TO_DB_PROVIDER = {"bkash": "1", "nagad": "2", "rocket": "3"}
DB_TO_JSON_PROVIDER = {v: k for k, v in JSON_TO_DB_PROVIDER.items()}


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


def load_base_dataset() -> dict:
    """Load base_dataset.json (the canonical login reference)."""
    path = Path(__file__).resolve().parent.parent / "base_dataset.json"
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ---------------- Seeder ----------------

def seed():
    create_all_tables()
    rng = random.Random(SEED)

    # Load the canonical JSON so DB ids match the login registry.
    dataset = load_base_dataset()
    json_agents = dataset.get("agent", [])
    json_officers = dataset.get("territory-officer", [])
    json_analysts = dataset.get("risk-analyst", [])
    json_assignments = dataset.get("agent-provider-assignment", [])
    json_transactions = dataset.get("transactions-stream", [])
    json_divisions = dataset.get("divisions", [])

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

        # 0. Providers — DB numeric ids 1/2/3, mapped from JSON "bkash"/"nagad"/"rocket"
        s.add_all([
            Provider(id="1", provider_name="bKash"),
            Provider(id="2", provider_name="Nagad"),
            Provider(id="3", provider_name="Rocket"),
        ])
        s.commit()

        # 1. Divisions — from JSON
        s.add_all([
            Division(div_id=d["div_id"], div_name=d["div_name"])
            for d in json_divisions
        ])
        s.commit()

        # 2. Risk analysts — canonical JSON ids, login matches by name.
        #    JSON doesn't carry area_name for analysts; derive it from the first
        #    territory office under that analyst (or fall back to the division name).
        officer_area_by_analyst = {}
        for o in json_officers:
            officer_area_by_analyst.setdefault(o["risk_analyst_id"], o["area_name"])
        div_name_by_id = {d["div_id"]: d["div_name"] for d in json_divisions}

        s.add_all([
            RiskAnalyst(
                id=a["id"],                              # "1".."9" — matches login
                name=a["name"],                          # "risk1".."risk9"
                provider_id=JSON_TO_DB_PROVIDER[a["provider_id"]],
                div_id=a["div_id"],
                area_name=officer_area_by_analyst.get(a["id"]) or div_name_by_id.get(a["div_id"], a["div_id"]),
                username=a["name"],                      # login matches by name
                password="123456",
            )
            for a in json_analysts
        ])
        s.commit()

        # 3. Territory offices — canonical JSON ids, login matches by name
        s.add_all([
            TerritoryOffice(
                id=o["id"],                              # "1".."27"
                name=o["name"],                          # "to1".."to27"
                provider_id=JSON_TO_DB_PROVIDER[o["provider_id"]],
                div_id=o["div_id"],
                area_name=o["area_name"],
                risk_analyst_id=o["risk_analyst_id"],
                username=o["name"],                      # login matches by name
                password="123456",
            )
            for o in json_officers
        ])
        s.commit()

        # 4. Agents — canonical JSON ids (e.g. "agent1", "agent1000")
        s.add_all([
            Agent(
                agent_id=a["agent_id"],                  # matches login exactly
                shop_name=a["shop_name"],
                area=a["area"],
                district=a["district"],
                shared_physical_cash=Decimal(a["shared_physical_cash"]),
                status=AgentStatus(a["status"]),
                username=a["agent_id"],                  # login matches by agent_id
                password="123456",
            )
            for a in json_agents
        ])

        # 5. Agent-provider assignments — pulled directly from JSON
        # (each agent serves 1..3 providers depending on entries)
        s.add_all([
            AgentProviderAssignment(
                agent_id=a["agent-id"],
                provider_id=JSON_TO_DB_PROVIDER[a["provider_id"]],
                to_officer_id=a["to-officer-id"],
            )
            for a in json_assignments
        ])
        s.commit()

        # 6. Wallets — one per (agent, assigned-provider), realistic balance bands
        wallet_records = []
        for asg in json_assignments:
            db_prov = JSON_TO_DB_PROVIDER[asg["provider_id"]]
            if db_prov == "1":
                bal = rng.randint(150_000, 450_000)  # bKash — bigger
            elif db_prov == "2":
                bal = rng.randint(80_000, 250_000)   # Nagad — mid
            else:
                bal = rng.randint(40_000, 150_000)   # Rocket — smaller
            wallet_records.append(
                ProviderWallet(
                    wallet_id=f"wallet_{asg['agent-id']}_{db_prov}",
                    agent_id=asg["agent-id"],
                    provider_id=db_prov,
                    e_money_balance=Decimal(bal),
                    last_sync_time=datetime.now(BD_TZ) - timedelta(minutes=rng.randint(1, 30)),
                )
            )
        s.add_all(wallet_records)
        s.commit()

        # 7. Historical transactions — load directly from JSON's transactions-stream,
        #    marking them as "Normal-Day" (older baseline) and re-stamping the timestamps
        #    so they're ~2 weeks old (consistent with the JSON's June 11 dates).
        #    Skip any malformed rows (e.g. placeholder entries that lack amount/tx_type).
        def _cust_hash(t: dict, idx: int) -> str:
            return t.get("customer_id_hash") or hash_customer(t.get("provider_id", "unk"), idx)

        json_tx_records = []
        skipped_json_tx = 0
        for idx, t in enumerate(json_transactions):
            if not all(k in t for k in ("tx_id", "agent_id", "provider_id", "timestamp", "tx_type", "amount")):
                skipped_json_tx += 1
                continue
            try:
                json_tx_records.append(
                    TransactionStream(
                        tx_id=t["tx_id"],
                        agent_id=t["agent_id"],
                        provider_id=JSON_TO_DB_PROVIDER[t["provider_id"]],
                        customer_id_hash=_cust_hash(t, idx),
                        tx_type=TransactionType(t["tx_type"]),
                        tx_day="Normal-Day",                # baseline (historical)
                        amount=Decimal(t["amount"]),
                        timestamp=datetime.fromisoformat(t["timestamp"]).replace(tzinfo=BD_TZ).astimezone(BD_TZ).replace(tzinfo=None),
                    )
                )
            except (KeyError, ValueError):
                skipped_json_tx += 1
        if skipped_json_tx:
            print(f"  (skipped {skipped_json_tx} malformed JSON transaction rows)")

        # 8. LIVE "Eid-eve" transactions — synthesized for the LAST 30 MINUTES.
        #    These are what the simulation engine and the dashboard "Live outflow/inflow"
        #    velocity read. Higher amounts, higher frequency (Eid-eve surge).
        now = datetime.now(BD_TZ)
        live_tx_records = []
        for wallet in wallet_records:
            for mins_ago in range(30, 0, -1):
                if rng.random() < 0.65:  # ~65% chance per minute — denser than baseline
                    ttype = rng.choices(
                        [TransactionType.CASH_IN, TransactionType.CASH_OUT],
                        weights=[0.40, 0.60],  # more cash-out on Eid-eve
                    )[0]
                    if ttype == TransactionType.CASH_OUT:
                        amount = rng.randint(3_000, 18_000)  # bigger than baseline
                    else:
                        amount = rng.randint(2_000, 12_000)
                    live_tx_records.append(
                        TransactionStream(
                            tx_id=f"live_{wallet.wallet_id}_{mins_ago}",
                            agent_id=wallet.agent_id,
                            provider_id=wallet.provider_id,
                            customer_id_hash=hash_customer(wallet.provider_id, rng.randint(1, 800)),
                            tx_type=ttype,
                            tx_day="Eid-eve",                 # live stream
                            amount=Decimal(amount),
                            timestamp=now - timedelta(minutes=mins_ago),
                        )
                    )

        s.add_all(json_tx_records)
        s.add_all(live_tx_records)
        s.commit()

        # 9. Demo alerts + tickets + audit logs — one per assignment so the dashboards
        #    have populated data to render after login.
        demo_alerts = []
        demo_tickets = []
        demo_audit = []
        for idx, asg in enumerate(json_assignments):
            agent_id = asg["agent-id"]
            provider_id = JSON_TO_DB_PROVIDER[asg["provider_id"]]
            officer_id = asg["to-officer-id"]

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

            # 70% OPEN, 20% ACKNOWLEDGED, 10% UNDER_REVIEW (none RESOLVED — keeps dashboards populated)
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
        print(f"[seed] OK (canonical ids from base_dataset.json)")
        print(f"  Divisions:        {len(s.exec(select(Division)).all())}")
        print(f"  Risk Analysts:    {len(s.exec(select(RiskAnalyst)).all())}")
        print(f"  Territory Offices:{len(s.exec(select(TerritoryOffice)).all())}")
        print(f"  Agents:           {len(s.exec(select(Agent)).all())}")
        print(f"  Assignments:      {len(s.exec(select(AgentProviderAssignment)).all())}")
        print(f"  Wallets:          {len(s.exec(select(ProviderWallet)).all())}")
        print(f"  Transactions:     {len(s.exec(select(TransactionStream)).all())}  "
              f"(JSON baseline + live Eid-eve)")
        print(f"  Alerts:           {len(s.exec(select(Alert)).all())}")
        print(f"  Tickets:          {len(s.exec(select(Ticket)).all())}")
        print(f"  Audit logs:       {len(s.exec(select(AuditLog)).all())}")

        print("\n[seed] Login handle examples (password is always '123456'):")
        for a in json_agents[:3]:
            print(f"  Agent:        username='{a['agent_id']}'   shop={a['shop_name']}")
        for o in json_officers[:3]:
            print(f"  Officer:      username='{o['name']}'       area={o['area_name']} ({DB_TO_JSON_PROVIDER.get(o['provider_id'], o['provider_id'])})")
        for an in json_analysts[:3]:
            print(f"  Risk Analyst: username='{an['name']}'      div={an['div_id']}")


if __name__ == "__main__":
    seed()