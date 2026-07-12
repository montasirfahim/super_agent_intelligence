"""App startup hooks — DB schema bootstrap + JSON-seed refresh.

Every FastAPI process boot:
1. Creates all SQLModel tables (idempotent).
2. Resets Agent.shared_physical_cash and ProviderWallet.e_money_balance
   back to base_dataset.json's seed values.
3. Wipes all `simlive_*` transactions (and the alerts / tickets /
   audit logs they generated) so the dashboard's per-card totals
   (deductions, additions, net, count, starting balance derived) all
   go back to zero — matching the freshly reset DB columns exactly.

Steps 2 and 3 are bundled so the user-visible state is internally
consistent: balance is at the seed value, AND the per-card totals
reflect zero simulated activity. Without step 3 the cards would still
show a "DRAINING" badge with a starting balance higher than the current
balance (because the persisted sim txns would still aggregate to a
non-zero net movement).

History rows from `transactions-stream` (the seeded history used by
the analytics engine baseline) are NOT deleted — only the simlive_
prefix, which is purely the test/demo-generated rows.
"""
import json
import logging
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import delete
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, SQLModel, select

from app.database import engine
from app.models.schema import *  # noqa: F401,F403

logger = logging.getLogger(__name__)

BASE_DATASET_PATH = Path(__file__).resolve().parent.parent / "base_dataset.json"

# base_dataset.json uses the lowercase provider labels ("bkash", "nagad",
# "rocket"); the DB stores them as numeric strings ("1", "2", "3").
_JSON_PROVIDER_TO_DB = {"bkash": "1", "nagad": "2", "rocket": "3"}


def _load_base_dataset() -> dict[str, Any]:
    """Read & parse base_dataset.json. Falls back to empty dict on
    missing file (logged) — startup must not crash if the JSON is
    missing during local dev."""
    if not BASE_DATASET_PATH.exists():
        logger.warning(
            "base_dataset.json not found at %s — skipping balance reset",
            BASE_DATASET_PATH,
        )
        return {}
    try:
        return json.loads(BASE_DATASET_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error("Failed to parse base_dataset.json: %s", e)
        return {}


def _coerce_decimal(raw: Any) -> Decimal:
    """base_dataset.json stores numbers as strings ("500000.00")."""
    if raw is None:
        return Decimal("0")
    try:
        return Decimal(str(raw))
    except Exception:
        return Decimal("0")


# Synthetic transaction prefixes wiped on every reset. Anything starting
# with these prefixes is test/demo-generated and must NOT survive a
# server restart — the user-visible state on a fresh startup must be
# exactly the JSON-seed values plus zero simulated activity.
#
#   simlive_*      — txns written by app/api/simulation.py (manual inject
#                    + background sim loop). Customer hash picked from
#                    base_dataset.json's transactions-stream.
#   live_wallet_*  — txns written by an earlier client-side seed path
#                    (now removed). Used synthetic cust_<sha256> hashes
#                    that defeat per-customer anomaly detection. Still in
#                    the DB from before that path was deleted; we wipe
#                    them here so they stop polluting the dashboard's
#                    "Recent transactions" table.
_SIM_TXN_PREFIXES = ("simlive_", "live_wallet_")


def _wipe_sim_only_artifacts() -> tuple[int, int, int, int]:
    """Delete sim-only artifacts so the dashboard's running totals reflect
    zero activity on a freshly-reset DB. Returns (sim_txns, alerts,
    audit_logs, tickets) counts that were deleted.

    Order matters — child rows first to satisfy foreign-key constraints:
      AuditLog → Ticket → Alert → TransactionStream (simlive/live_wallet prefix)

    The Alert master_evidence_json blob references the per-provider
    tickets, so deleting the alert also drops the case from the
    analyst's queue. AuditLog rows keep orphaning if we just delete the
    ticket without its logs, hence the explicit first step.
    """
    deleted_audit = deleted_tickets = deleted_alerts = deleted_txns = 0
    try:
        with Session(engine) as s:
            # 1) AuditLog rows for tickets that came from sim-generated alerts.
            #    Identify by joining with Alert where the alert's evidence
            #    was created by the simulation engine — flagged by the
            #    CREATED-by-simulation notes_text.
            audit_rows = s.exec(
                select(AuditLog)
                .where(AuditLog.notes_text.like("%CREATED by simulation%"))
            ).all()
            audit_ids = {r.log_id for r in audit_rows}
            if audit_ids:
                for r in audit_rows:
                    s.delete(r)
                deleted_audit = len(audit_rows)
            s.commit()

            # 2) Tickets for alerts that originated from sim evidence.
            #    We identify those by Alert.master_evidence_json containing
            #    'simlive_' (the engine only synthesises that prefix from
            #    the simulation loop). Cheaper than joining — single scan.
            ticket_rows = s.exec(
                select(Ticket).join(Alert, Ticket.alert_id == Alert.alert_id)
                .where(Alert.master_evidence_json.like("%simlive_%"))
            ).all()
            if ticket_rows:
                for t in ticket_rows:
                    s.delete(t)
                deleted_tickets = len(ticket_rows)
            s.commit()

            # 3) The originating alerts themselves.
            alert_rows = s.exec(
                select(Alert).where(Alert.master_evidence_json.like("%simlive_%"))
            ).all()
            if alert_rows:
                for a in alert_rows:
                    s.delete(a)
                deleted_alerts = len(alert_rows)
            s.commit()

            # 4) The sim-generated transactions themselves. Wipe BOTH
            #    prefixes — simlive_* (current sim loop) and live_wallet_*
            #    (legacy synthetic rows from a deprecated seed path).
            from sqlalchemy import or_
            txn_rows = s.exec(
                select(TransactionStream)
                .where(
                    or_(*[TransactionStream.tx_id.like(p + "%") for p in _SIM_TXN_PREFIXES])
                )
            ).all()
            if txn_rows:
                for t in txn_rows:
                    s.delete(t)
                deleted_txns = len(txn_rows)
            s.commit()
    except OperationalError as e:
        logger.warning("DB unavailable during sim-artifact wipe: %s", e)
    except Exception as e:
        logger.error("Sim-artifact wipe failed: %s", e)
    return deleted_txns, deleted_alerts, deleted_tickets, deleted_audit


def reset_balances_from_seed() -> None:
    """Reset Agent.shared_physical_cash, every ProviderWallet balance,
    AND wipe sim-only artifacts (simlive txns + alerts/tickets/logs
    generated from them). Called at app startup.

    Walks `agent[*].shared_physical_cash` and
    `providerwallet[*].e_money_balance` from base_dataset.json, looks
    up the corresponding DB row by primary key, and overwrites the
    balance column. After the column reset we wipe every row with a
    `simlive_` prefix so the dashboard's per-card totals (which the
    server aggregates server-side from TransactionStream) start at zero
    — guaranteeing the visible state on a fresh startup is internally
    consistent: balance equals starting balance equals zero drained.

    The seeded history rows (whose `tx_id` does NOT start with
    `simlive_`, e.g. `tx1`, `tx2`, …) are NOT touched.
    """
    data = _load_base_dataset()
    if not data:
        logger.info("Skipping balance reset: empty base_dataset.json")
        # Still wipe sim artifacts even if JSON is missing — those
        # are pure test/demo rows and we want a clean slate.
        txn_d, alert_d, ticket_d, audit_d = _wipe_sim_only_artifacts()
        logger.info(
            "Wiped sim artifacts: %d txns, %d alerts, %d tickets, %d audit logs",
            txn_d, alert_d, ticket_d, audit_d,
        )
        return

    try:
        with Session(engine) as s:
            agent_count = wallet_count = 0
            # Reset Agent.shared_physical_cash
            for a in data.get("agent", []):
                agent_id = a.get("agent_id")
                if not agent_id:
                    continue
                row = s.get(Agent, agent_id)
                if row is None:
                    continue
                new_val = _coerce_decimal(a.get("shared_physical_cash"))
                if row.shared_physical_cash != new_val:
                    row.shared_physical_cash = new_val
                    s.add(row)
                    agent_count += 1
            s.commit()

            # Reset ProviderWallet.e_money_balance
            # JSON stores provider_id as "bkash"/"nagad"/"rocket"; the DB
            # column stores the numeric form "1"/"2"/"3". Translate before
            # the lookup so we hit the right row.
            for w in data.get("providerwallet", []):
                agent_id = w.get("agent_id")
                prov_json = w.get("provider_id")
                if not agent_id or not prov_json:
                    continue
                provider_id = _JSON_PROVIDER_TO_DB.get(prov_json, prov_json)
                row = s.exec(
                    select(ProviderWallet)
                    .where(ProviderWallet.agent_id == agent_id)
                    .where(ProviderWallet.provider_id == provider_id)
                ).first()
                if row is None:
                    continue
                new_val = _coerce_decimal(w.get("e_money_balance"))
                if row.e_money_balance != new_val:
                    row.e_money_balance = new_val
                    s.add(row)
                    wallet_count += 1
            s.commit()
            logger.info(
                "Reset balances from base_dataset.json: "
                "%d agents, %d wallets updated",
                agent_count, wallet_count,
            )
    except OperationalError as e:
        logger.warning("DB unavailable during balance reset: %s", e)
    except Exception as e:
        logger.error("Balance reset failed: %s", e)

    # ALWAYS wipe sim-only artifacts (separate session/connection —
    # gives the audit-log / ticket rows a clean slate even if the
    # balance reset above errored on one specific row).
    txn_d, alert_d, ticket_d, audit_d = _wipe_sim_only_artifacts()
    logger.info(
        "Wiped sim artifacts: %d txns, %d alerts, %d tickets, %d audit logs",
        txn_d, alert_d, ticket_d, audit_d,
    )


def create_all_tables() -> None:
    """Idempotent. SQLModel.metadata.create_all only creates missing tables
    and never drops existing ones, so calling this on every boot is safe."""
    try:
        SQLModel.metadata.create_all(engine)
        logger.info("Database tables created successfully")
    except OperationalError as exc:
        logger.warning("Database unavailable during startup: %s", exc)


def startup() -> None:
    """Combined startup: schema bootstrap + JSON-seed balance refresh +
    sim-artifact wipe."""
    create_all_tables()
    reset_balances_from_seed()


if __name__ == "__main__":
    # Allow `python -m app.startup` for ad-hoc reset during dev.
    logging.basicConfig(level=logging.INFO)
    startup()
