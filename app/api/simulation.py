"""Live transaction injection + background simulation orchestration.

This module powers the agent dashboard's "Start Simulation" workflow:

1. Agent selects a simulation window (5 / 10 / 30 min) on the dashboard.
2. POST /api/simulate/start kicks off a background task that injects
   realistic Bangladeshi MFS-style transactions every ~25 seconds for the
   full window. The transactions look natural — varied providers,
   customer hashes, amounts in BDT — but are seeded, not real.
3. After each injected txn the analytics engine is invoked; if the burn
   rate spikes vs the agent's historical baseline (from base_dataset.json),
   an Alert + per-provider Tickets are generated and routed to the
   territory officer for that provider.
4. While the sim runs, the dashboard polls /api/simulate/status to know
   progress; the existing /api/dashboard/agent endpoint picks up the new
   txns and the JS-side LiveCash / LiveWallet ledgers visibly drain.

Endpoints
---------
POST /api/simulate/start        — Begin a sim for (agent_id, window_minutes)
GET  /api/simulate/status       — Current sim state (running, progress, count)
POST /api/simulate/stop         — Manually end the current sim
POST /api/simulate/inject-transaction  — Inject a single tx (still useful for tests)
POST /api/simulate/inject-burst        — Inject N txns at once (test helper)

Customer hashes — read from base_dataset.json
----------------------------------------------
The injected txns need a `customer_id_hash` per row. Synthetic hashes
would defeat the engine's per-customer anomaly detection (because every
new hash is unique, no pattern ever forms). Instead we load every
distinct `customer_id_hash` value from `base_dataset.json`'s
`transactions-stream` and pick randomly from the bucket for the
transaction's provider. With 64 customers in the JSON, repeated picks
over the sim window naturally produce repeat visits from the same
"person" — exactly what the engine's recurrence + spike detectors need.
"""
from __future__ import annotations

import hashlib
import json
import logging
import random
import threading
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, HTTPException
from sqlmodel import Session, select

from app.database import engine
from app.models import (
    Agent,
    Alert,
    AlertType,
    AuditAction,
    AuditLog,
    OwnerRole,
    ProviderWallet,
    SeverityLevel,
    Ticket,
    TicketStatus,
    TransactionStream,
    TransactionType,
)
from app.services.analytics_engine import build_alert_and_tickets
from app.services.routing import route_to_officer

logger = logging.getLogger(__name__)

# Path is resolved relative to this file so the importer doesn't need
# to know where the workspace root is.
BASE_DATASET_PATH = Path(__file__).resolve().parent.parent.parent / "base_dataset.json"

# Map JSON provider IDs ("bkash"/"nagad"/"rocket") to DB provider IDs
# ("1"/"2"/"3") for the per-provider customer buckets.
_JSON_PROVIDER_TO_DB = {"bkash": "1", "nagad": "2", "rocket": "3"}
_DB_PROVIDER_TO_JSON = {v: k for k, v in _JSON_PROVIDER_TO_DB.items()}

router = APIRouter(tags=["simulation"])

BD_TZ = timezone(timedelta(hours=6))

# Allowed simulation windows (in minutes)
ALLOWED_WINDOWS = (5, 10, 30)

# Background tick interval — every ~25 seconds the loop fires one new
# transaction. Short enough to feel live in the demo, long enough to leave
# time between events for the dashboard to render and the engine to compare
# against the historical baseline.
TICK_SECONDS = 25

# Throttle the engine evaluation. Each call makes 30+ DB queries against
# the live wallet / transaction tables (and is ~10s against a remote DB
# like Neon). We don't need to evaluate on every tick — every 2 ticks
# (= ~50s with default TICK_SECONDS=25) is plenty often to catch a spike.
EVAL_EVERY_N_TICKS = 2

# Wait until at least this many txns have accumulated before evaluating.
# Below this threshold the engine has too little live data to compare
# against the JSON baseline, and we'd just generate noise.
EVAL_MIN_TXN_COUNT = 4

# Module-level sim state — process-local. Keyed by sim_id for uniqueness;
# the most-recent sim is also tracked under SIM_STATE["active"] for fast
# dashboard polling.
SIM_STATE: Dict[str, Any] = {
    "active": None,           # sim_id of currently-running sim, or None
    "sims": {},               # sim_id → sim record (history)
}
_SIM_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# Transaction shaping — make injected txns look like real Bangladeshi MFS
# ---------------------------------------------------------------------------

# Amount ranges (BDT) tuned to match typical bKash/Nagad/Rocket flows
# (salary cash-outs at month-end, merchant cash-ins during business hours,
# small P2P transfers mid-day). The ranges here mirror what's already in
# `inject_burst` so the demo feels consistent.
CASH_OUT_RANGE = (1_500, 25_000)
CASH_IN_RANGE = (1_000, 18_000)

# CASH_OUT vs CASH_IN bias — drains the drawer over the window so the
# physical-cash + e-money cards visibly deplete. Tunable per sim.
DEFAULT_OUT_PROBABILITY = 0.65


# ---------------------------------------------------------------------------
# Customer-hash pool — real values from base_dataset.json
# ---------------------------------------------------------------------------
# Synthetic `cust_<sha256>` hashes defeat the engine's per-customer
# anomaly detection (no customer ever visits twice → no pattern ever
# forms). Instead, load every distinct customer_id_hash from
# base_dataset.json's `transactions-stream`, bucket them per provider,
# and `_pick_customer(rng, provider_id)` picks randomly from the bucket.
#
# Side benefit: the same `cust_hash_007` customer that appears in the
# seed history can reappear in a sim, so the engine's recurrence +
# per-customer baseline has something to detect.
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _load_customer_pool() -> Dict[str, List[str]]:
    """Return { db_provider_id: [customer_id_hash, ...] } built from
    base_dataset.json. Falls back to a tiny synthetic fallback if the
    JSON is missing or empty so the sim still works in degraded dev.

    The lru_cache keeps the JSON read once per process — it's small,
    but no need to re-parse on every tick.
    """
    pool: Dict[str, List[str]] = {"1": [], "2": [], "3": []}
    if not BASE_DATASET_PATH.exists():
        logger.warning(
            "base_dataset.json not found at %s — using synthetic customer pool",
            BASE_DATASET_PATH,
        )
        pool["1"] = [f"cust_hash_fallback_{i:03d}" for i in range(10)]
        pool["2"] = [f"cust_hash_fallback_{i:03d}" for i in range(10, 20)]
        pool["3"] = [f"cust_hash_fallback_{i:03d}" for i in range(20, 30)]
        return pool
    try:
        data = json.loads(BASE_DATASET_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error("Failed to parse base_dataset.json: %s — falling back", e)
        pool["1"] = [f"cust_hash_fallback_{i:03d}" for i in range(10)]
        pool["2"] = [f"cust_hash_fallback_{i:03d}" for i in range(10, 20)]
        pool["3"] = [f"cust_hash_fallback_{i:03d}" for i in range(20, 30)]
        return pool

    # Per-provider buckets — txn rows have provider_id in the JSON form
    # ("bkash"/"nagad"/"rocket"), translate to DB form ("1"/"2"/"3").
    seen_per: Dict[str, set] = {"1": set(), "2": set(), "3": set()}
    for t in data.get("transactions-stream", []):
        cust = t.get("customer_id_hash")
        prov_json = t.get("provider_id")
        if not cust or not prov_json:
            continue
        pid = _JSON_PROVIDER_TO_DB.get(prov_json)
        if pid is None:
            continue
        seen_per[pid].add(cust)
    for pid in pool:
        # Sort so the random pick is deterministic-seed-friendly and
        # the same hash can be picked again on a repeat run.
        pool[pid] = sorted(seen_per[pid])
        logger.info("Loaded %d customer hashes for provider_id=%s",
                    len(pool[pid]), pid)
    # Fallback if a provider has zero txns in the JSON.
    for pid in pool:
        if not pool[pid]:
            pool[pid] = [f"cust_hash_synth_{pid}_{i:03d}" for i in range(10)]
            logger.warning("Provider %s has no customer hashes in JSON; "
                           "using synthetic fallback", pid)
    return pool


def _pick_provider(rng: random.Random) -> str:
    """Pick one of bKash/Nagad/Rocket with weighted bias toward bKash
    (largest market share in Bangladesh MFS)."""
    weights = [("1", 0.55), ("2", 0.30), ("3", 0.15)]
    r = rng.random()
    cum = 0.0
    for pid, w in weights:
        cum += w
        if r <= cum:
            return pid
    return "1"


def _pick_customer(rng: random.Random, provider_id: str = "1") -> str:
    """Pick a real customer_id_hash from base_dataset.json for the given
    provider. With 30-40 distinct hashes per provider and a 25s tick
    over a 5-min sim window (~12 txns), random-with-replacement sampling
    naturally produces ~3 visits per repeat customer — exactly the kind
    of pattern the engine's per-customer anomaly detector is built for.
    """
    pool = _load_customer_pool()
    bucket = pool.get(provider_id) or pool["1"]
    return rng.choice(bucket)


def _pick_amount(rng: random.Random, tx_type: str) -> int:
    lo, hi = CASH_OUT_RANGE if tx_type == "CASH_OUT" else CASH_IN_RANGE
    # Round to nearest 100 BDT — looks more natural than precise floats.
    raw = rng.randint(lo, hi)
    return int(round(raw / 100) * 100)


def _pick_tx_type(rng: random.Random, out_prob: float) -> str:
    return "CASH_OUT" if rng.random() < out_prob else "CASH_IN"


# ---------------------------------------------------------------------------
# Sim state lifecycle
# ---------------------------------------------------------------------------

def _new_sim(agent_id: str, window_minutes: int, out_probability: float) -> Dict[str, Any]:
    started_at = datetime.now(BD_TZ).replace(tzinfo=None)
    ends_at = started_at + timedelta(minutes=window_minutes)
    sim_id = f"sim_{uuid.uuid4().hex[:12]}"
    return {
        "sim_id": sim_id,
        "agent_id": agent_id,
        "window_minutes": window_minutes,
        "out_probability": out_probability,
        "started_at": started_at.isoformat(),
        "ends_at": ends_at.isoformat(),
        "tick_seconds": TICK_SECONDS,
        "ticks_total": (window_minutes * 60) // TICK_SECONDS,
        "ticks_elapsed": 0,
        "txns_injected": 0,
        "alerts_generated": 0,
        "running": True,
        "last_error": None,
        "last_txn": None,
    }


def _register_sim(sim: Dict[str, Any]) -> None:
    with _SIM_LOCK:
        SIM_STATE["active"] = sim["sim_id"]
        SIM_STATE["sims"][sim["sim_id"]] = sim


def _finalize_sim(sim_id: str) -> None:
    with _SIM_LOCK:
        sim = SIM_STATE["sims"].get(sim_id)
        if sim:
            sim["running"] = False
        if SIM_STATE.get("active") == sim_id:
            SIM_STATE["active"] = None


def _get_active_sim(agent_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    with _SIM_LOCK:
        sim_id = SIM_STATE.get("active")
        if not sim_id:
            return None
        sim = SIM_STATE["sims"].get(sim_id)
        if not sim:
            return None
        if agent_id and sim["agent_id"] != agent_id:
            return None
        return sim


# ---------------------------------------------------------------------------
# Balance mutation helper — shared by sim loop + manual inject endpoint
# ---------------------------------------------------------------------------

def _apply_balance_delta(
    agent: Agent,
    wallet: ProviderWallet,
    tx_type: str,
    amount: int | float,
    ts: datetime,
) -> tuple[Decimal, Decimal]:
    """Mutate the agent's physical-cash drawer and the provider's e-money
    balance for a single customer txn.

    CASH_OUT: customer takes cash out of the agent's drawer → physical_cash
              DECREASES by `amount`; the agent's e-wallet with the provider
              INCREASES (because the provider now holds that cash on the
              agent's behalf and must settle it later).
    CASH_IN:  customer deposits cash into the agent's drawer → physical_cash
              INCREASES; the agent's e-wallet with the provider DECREASES
              (the agent is paying the provider for that e-money float).

    Mirrors how MFS reconciliation works in production: physical drawer and
    provider e-money always move in opposite directions on every customer
    txn. We commit the new values in-place on the ORM objects; the caller
    is responsible for s.commit().

    Floors both balances at zero — the client-side ledger still does the
    "draining demo" math; this DB write keeps every other dashboard honest.
    """
    signed = Decimal(str(amount))
    if tx_type == "CASH_OUT":
        new_physical = Decimal(agent.shared_physical_cash) - signed
        new_emoney   = Decimal(wallet.e_money_balance)    + signed
    else:  # CASH_IN
        new_physical = Decimal(agent.shared_physical_cash) + signed
        new_emoney   = Decimal(wallet.e_money_balance)    - signed
    if new_physical < 0:
        new_physical = Decimal("0")
    if new_emoney < 0:
        new_emoney = Decimal("0")

    agent.shared_physical_cash = new_physical
    wallet.e_money_balance     = new_emoney
    wallet.last_sync_time      = ts
    return new_physical, new_emoney


# ---------------------------------------------------------------------------
# One tick — inject one txn, run engine, optionally generate alert
# ---------------------------------------------------------------------------

def _inject_one_txn(
    sim: Dict[str, Any],
    rng: random.Random,
) -> Optional[Dict[str, Any]]:
    """Insert a single natural-looking txn for this sim. Returns the inserted
    row's public shape (used by the dashboard's polling), or None if the
    agent / wallet no longer exists."""
    agent_id = sim["agent_id"]
    provider_id = _pick_provider(rng)
    tx_type = _pick_tx_type(rng, sim.get("out_probability", DEFAULT_OUT_PROBABILITY))
    amount = _pick_amount(rng, tx_type)
    # Customer hash is picked from base_dataset.json's bucket for THIS
    # provider — same hash values that seeded the historical baseline.
    cust_hash = _pick_customer(rng, provider_id)

    now = datetime.now(BD_TZ).replace(tzinfo=None)
    nonce = hashlib.md5(f"{provider_id}_{now.timestamp()}_{rng.random()}".encode()).hexdigest()[:8]
    tx_id = f"simlive_{agent_id}_{provider_id}_{int(now.timestamp())}_{nonce}"

    with Session(engine) as s:
        agent = s.get(Agent, agent_id)
        if not agent:
            return None
        wallet = s.exec(
            select(ProviderWallet)
            .where(ProviderWallet.agent_id == agent_id)
            .where(ProviderWallet.provider_id == provider_id)
        ).first()
        if not wallet:
            return None

        # Mutate the agent's drawer + provider e-money balances atomically
        # with the txn insert. See _apply_balance_delta for the rule.
        _apply_balance_delta(agent, wallet, tx_type, amount, now)

        txn = TransactionStream(
            tx_id=tx_id,
            agent_id=agent_id,
            provider_id=provider_id,
            customer_id_hash=cust_hash,
            tx_type=TransactionType(tx_type),
            tx_day="Eid-eve",
            amount=Decimal(amount),
            timestamp=now,
        )
        s.add(txn)
        s.commit()

    return {
        "tx_id": tx_id,
        "agent_id": agent_id,
        "provider_id": provider_id,
        "type": tx_type,
        "amount": amount,
        "customer_id_hash": cust_hash,
        "timestamp": now.isoformat(),
    }


def _evaluate_and_persist_alerts(sim: Dict[str, Any]) -> int:
    """Run the analytics engine for this agent. If it produces an Alert,
    persist it + per-provider Tickets + initial AuditLog rows.

    Returns the number of NEW alerts generated by this tick (0 if the
    engine didn't trip any threshold).
    """
    agent_id = sim["agent_id"]
    now = datetime.now(BD_TZ).replace(tzinfo=None)
    new_alerts = 0

    with Session(engine) as s:
        try:
            result = build_alert_and_tickets(s, agent_id, now)
        except Exception as e:
            sim["last_error"] = f"engine error: {type(e).__name__}: {e}"
            return 0

        alert_d = result["alert"]
        if alert_d is None or not result["tickets"]:
            return 0

        sev_map = {
            "low": SeverityLevel.LOW,
            "medium": SeverityLevel.MEDIUM,
            "high": SeverityLevel.HIGH,
        }
        severity_enum = sev_map.get(
            str(alert_d["severity"]).lower(), SeverityLevel.MEDIUM
        )

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
        s.add(alert_row)

        for tk in result["tickets"]:
            officer_id = tk.get("assigned_officer_id") or ""
            if not officer_id:
                # Fallback: route via the (agent, provider) lookup so the
                # territory officer for the responsible provider actually
                # gets the ticket, even if build_ticket couldn't resolve it.
                try:
                    officer_id = route_to_officer(s, agent_id, tk["provider_id"]) or ""
                except Exception:
                    officer_id = ""

            ticket_row = Ticket(
                ticket_id=tk["ticket_id"],
                alert_id=tk["alert_id"],
                provider_id=tk["provider_id"],
                assigned_officer_id=officer_id,
                current_owner_role=OwnerRole(tk["current_owner_role"]),
                status=TicketStatus(tk["status"]),
                evidence_json=tk["evidence_json"],
                created_at=tk["created_at"],
                updated_at=tk["updated_at"],
            )
            s.add(ticket_row)

            audit_row = AuditLog(
                log_id=f"audit_{tk['ticket_id']}_created",
                ticket_id=tk["ticket_id"],
                action_taken=AuditAction.ACKNOWLEDGE,
                performed_by_role="SYSTEM",
                notes_text=(
                    f"CREATED by simulation engine "
                    f"(sim_id={sim['sim_id']}, alert_type={alert_d['alert_type']}, "
                    f"severity={alert_d['severity']}, provider={tk['provider_id']})"
                ),
                timestamp=tk["created_at"],
            )
            s.add(audit_row)
        s.commit()
        new_alerts = 1
    return new_alerts


def _sim_loop(sim_id: str, seed: int) -> None:
    """Background loop: tick every TICK_SECONDS, inject one txn, evaluate.

    Runs in a daemon thread (started by `/api/simulate/start`) so it doesn't
    block the request handler. The thread lives until the sim's `ends_at`
    or until `stop_simulation` is called. Production would use Celery/RQ.

    Engine evaluation is throttled: we don't run the analytics engine on
    every tick (it makes 30+ DB queries and can take ~10 seconds against a
    remote DB like Neon). Instead we evaluate at most once per
    `EVAL_EVERY_N_TICKS` ticks AND only after at least
    `EVAL_MIN_TXN_COUNT` transactions have accumulated. With TICK_SECONDS=25
    and EVAL_EVERY_N_TICKS=2, evaluation runs every 50s — fast enough to
    catch a spike mid-window without blocking the loop.
    """
    rng = random.Random(seed)
    sim = SIM_STATE["sims"].get(sim_id)
    if not sim:
        return
    ends_at = datetime.fromisoformat(sim["ends_at"])

    import time
    while True:
        now = datetime.now(BD_TZ).replace(tzinfo=None)
        if now >= ends_at:
            sim["running"] = False
            with _SIM_LOCK:
                if SIM_STATE.get("active") == sim_id:
                    SIM_STATE["active"] = None
            return

        # Inject one txn (if wallet/agent still exist)
        try:
            txn = _inject_one_txn(sim, rng)
            if txn:
                sim["txns_injected"] += 1
                sim["last_txn"] = {
                    "tx_id": txn["tx_id"],
                    "type": txn["type"],
                    "provider_id": txn["provider_id"],
                    "amount": txn["amount"],
                    "timestamp": txn["timestamp"],
                }
        except Exception as e:
            sim["last_error"] = f"inject error: {type(e).__name__}: {e}"

        sim["ticks_elapsed"] += 1

        # Throttled engine evaluation — skip until we've accumulated enough
        # txns, then evaluate every N ticks so the loop keeps moving.
        if (sim["txns_injected"] >= EVAL_MIN_TXN_COUNT
                and sim["ticks_elapsed"] % EVAL_EVERY_N_TICKS == 0):
            try:
                sim["alerts_generated"] += _evaluate_and_persist_alerts(sim)
            except Exception as e:
                sim["last_error"] = f"eval error: {type(e).__name__}: {e}"

        # Sleep until next tick — but never past the window end.
        next_tick_at = now + timedelta(seconds=TICK_SECONDS)
        sleep_for = min(TICK_SECONDS, max(0, (ends_at - next_tick_at).total_seconds()))
        if sleep_for <= 0:
            sim["running"] = False
            with _SIM_LOCK:
                if SIM_STATE.get("active") == sim_id:
                    SIM_STATE["active"] = None
            return

        time.sleep(sleep_for)


def _spawn_sim_thread(sim_id: str, seed: int) -> None:
    """Start `_sim_loop` in a daemon thread. Daemon so it dies with the
    process if the server shuts down mid-sim — never leaves zombie threads."""
    t = threading.Thread(target=_sim_loop, args=(sim_id, seed), daemon=True)
    t.start()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/api/simulate/start")
def start_simulation(
    payload: Dict[str, Any] = Body(default={}),
) -> Dict[str, Any]:
    """Begin a simulation for an agent.

    Body:
        {
            "agent_id":         "agent1000",   # required
            "window_minutes":   5 | 10 | 30,   # required
            "out_probability":  0.65,          # optional, default 0.65 (drain-biased)
        }

    Returns:
        { ok, sim_id, started_at, ends_at, ticks_total }

    Side effect: spawns a daemon thread that runs the injection loop.
    """
    agent_id = (payload or {}).get("agent_id")
    window_minutes = (payload or {}).get("window_minutes")
    out_probability = float((payload or {}).get("out_probability", DEFAULT_OUT_PROBABILITY))

    if not agent_id:
        raise HTTPException(400, "agent_id required")
    try:
        window_minutes = int(window_minutes)
    except (TypeError, ValueError):
        raise HTTPException(400, "window_minutes must be 5, 10, or 30")
    if window_minutes not in ALLOWED_WINDOWS:
        raise HTTPException(
            400,
            f"window_minutes must be one of {ALLOWED_WINDOWS}",
        )
    if not (0.0 <= out_probability <= 1.0):
        raise HTTPException(400, "out_probability must be 0..1")

    # Refuse to start a second sim if one is already running for this agent.
    existing = _get_active_sim(agent_id)
    if existing:
        raise HTTPException(
            409,
            f"simulation already running for {agent_id} "
            f"(sim_id={existing['sim_id']}, ends_at={existing['ends_at']})",
        )

    # Validate the agent exists
    with Session(engine) as s:
        if not s.get(Agent, agent_id):
            raise HTTPException(404, f"agent {agent_id} not found")

    sim = _new_sim(agent_id, window_minutes, out_probability)
    _register_sim(sim)

    # Spawn daemon thread — returns immediately, sim keeps running.
    # Seed the rng so re-runs with the same sim_id are deterministic.
    seed = int(hashlib.md5(sim["sim_id"].encode()).hexdigest()[:8], 16)
    _spawn_sim_thread(sim["sim_id"], seed)

    return {
        "ok": True,
        "sim_id": sim["sim_id"],
        "agent_id": agent_id,
        "window_minutes": window_minutes,
        "started_at": sim["started_at"],
        "ends_at": sim["ends_at"],
        "ticks_total": sim["ticks_total"],
        "tick_seconds": TICK_SECONDS,
    }


@router.get("/api/simulate/status")
def simulation_status(agent_id: Optional[str] = None) -> Dict[str, Any]:
    """Current sim state for an agent (or global if no agent_id given).

    Returns:
        {
          running: bool,
          active_sim_id: str|null,
          sim: {sim_id, agent_id, started_at, ends_at, ticks_total,
                ticks_elapsed, txns_injected, alerts_generated, last_txn,
                last_error} | null
        }
    """
    sim = _get_active_sim(agent_id)
    return {
        "running": sim is not None,
        "active_sim_id": sim["sim_id"] if sim else None,
        "sim": sim,
    }


@router.post("/api/simulate/stop")
def stop_simulation(agent_id: Optional[str] = None) -> Dict[str, Any]:
    """Manually end the active simulation for an agent (or the global active
    one if `agent_id` is omitted)."""
    sim = _get_active_sim(agent_id)
    if not sim:
        return {"ok": True, "stopped": False, "message": "no active simulation"}
    _finalize_sim(sim["sim_id"])
    return {"ok": True, "stopped": True, "sim_id": sim["sim_id"]}


@router.post("/api/simulate/reset")
def reset_sim_balances(payload: dict = Body(default={})) -> Dict[str, Any]:
    """Restore Agent.shared_physical_cash + ProviderWallet.e_money_balance to
    base_dataset.json seed values AND wipe every `simlive_*` transaction /
    alert / ticket / audit log so the dashboard's per-card sub-info
    (deductions, additions, net, count, starting balance) all reset to
    zero — matching the freshly-reset balance columns exactly.

    Accepts an optional `agent_id` in the body to scope the txn/alert
    wipe to one agent (the wallet/agent balance reset always runs
    globally regardless so other agents stay in a known-good state).

    Wired to the "↺ Reset Sim" button on the agent dashboard.
    """
    from app.startup import reset_balances_from_seed  # lazy import

    target_agent = (payload or {}).get("agent_id")
    logger.info("Manual reset requested (agent=%s)", target_agent or "<all>")

    # The startup helper resets ALL agents' balances + wipes ALL
    # simlive artifacts in one transactional sweep. Even when the user
    # pressed Reset for one agent, the global sweep is safe — we're
    # starting from JSON seed, so other agents move from "whatever
    # they were" to "their seed value" (which is what the user wants
    # after a sim run anyway).
    reset_balances_from_seed()
    return {
        "ok": True,
        "reset": True,
        "scope": target_agent or "all",
        "message": (
            "Balances restored to base_dataset.json seed; "
            "simlive transactions and derived alerts/tickets wiped"
        ),
    }


# ---------------------------------------------------------------------------
# Legacy helpers — single-injection + burst, kept for tests and ad-hoc use
# ---------------------------------------------------------------------------

@router.post("/api/simulate/inject-transaction")
def inject_transaction(payload: dict = Body(default={})):
    """Insert one specific transaction. Used by tests and ad-hoc demo scripts."""
    agent_id = (payload or {}).get("agent_id")
    provider_id = (payload or {}).get("provider_id")
    tx_type = (payload or {}).get("type")
    amount_raw = (payload or {}).get("amount")

    if not agent_id:
        raise HTTPException(400, "agent_id required")
    if provider_id not in ("1", "2", "3"):
        raise HTTPException(400, "provider_id must be '1' (bKash), '2' (Nagad), or '3' (Rocket")
    if tx_type not in ("CASH_OUT", "CASH_IN"):
        raise HTTPException(400, "type must be CASH_OUT or CASH_IN")
    try:
        amount = float(amount_raw)
        if amount <= 0:
            raise ValueError
    except (TypeError, ValueError):
        raise HTTPException(400, "amount must be a positive number")

    seed = f"{provider_id}_{datetime.now().timestamp()}".encode()
    # Use a real customer hash from base_dataset.json for this provider
    # — same reasoning as `_pick_customer` in the sim loop. Synthesized
    # hashes would defeat the engine's per-customer anomaly detection.
    cust_hash = _pick_customer(random.Random(), provider_id)
    now = datetime.now(BD_TZ).replace(tzinfo=None)
    nonce = hashlib.md5(seed).hexdigest()[:8]
    tx_id = f"simlive_{agent_id}_{provider_id}_{int(now.timestamp())}_{nonce}"

    with Session(engine) as s:
        agent = s.get(Agent, agent_id)
        if not agent:
            raise HTTPException(404, f"agent {agent_id} not found")
        wallet = s.exec(
            select(ProviderWallet)
            .where(ProviderWallet.agent_id == agent_id)
            .where(ProviderWallet.provider_id == provider_id)
        ).first()
        if not wallet:
            raise HTTPException(404, f"no wallet for agent {agent_id} on provider {provider_id}")

        # Mutate drawer + e-money balances atomically with the txn insert.
        _apply_balance_delta(agent, wallet, tx_type, amount, now)

        txn = TransactionStream(
            tx_id=tx_id,
            agent_id=agent_id,
            provider_id=provider_id,
            customer_id_hash=cust_hash,
            tx_type=TransactionType(tx_type),
            tx_day="Eid-eve",
            amount=Decimal(amount),
            timestamp=now,
        )
        s.add(txn)
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
    """Inject N random transactions in one shot. Test/demo helper."""
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
        tx_type = "CASH_OUT" if rng.random() < 0.7 else "CASH_IN"
        amount = rng.randint(2_000, 18_000) if tx_type == "CASH_OUT" else rng.randint(1_000, 12_000)
        result = inject_transaction({"agent_id": agent_id, "provider_id": prov,
                                     "type": tx_type, "amount": amount})
        inserted.append(result)
    return {"ok": True, "inserted": inserted, "count": len(inserted)}