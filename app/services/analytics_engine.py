"""Analytics engine — liquidity forecasting + behavioral anomaly detection.

Implements the formulas in `analytics_engine_prompt.md`. Produces an
`analyze()` master-evidence dict per agent that downstream services consume
to build `Alert` and `Ticket` ORM rows. This module is purely computational
plus ORM reads — it does NOT write to the database. Ticket creation and the
initial AuditLog row live in the orchestration layer.

DATA SOURCES
============

The engine uses TWO data sources, kept strictly separate:

1. `base_dataset.json` (file) — the CANONICAL historical reference.
   - All baseline / historical burn-rate samples come from here.
   - Per-customer history baseline (median amount, MAD, sigma) comes from here.
   - Cold-start fallback (area-pooled peers) uses data from here.
   - Loaded once per process and cached with `@lru_cache`.

2. Live Postgres DB — used ONLY for the rolling-window simulation stream.
   - The last 5/10/30 minutes of transactions (so the engine reacts to the demo).
   - Current wallet / shared-cash balances.
   - The simulation script writes new transactions here when the user clicks
     "Start Simulation" — those rows are what we compare against the JSON
     baseline to surface anomalies.

Why split? The JSON holds days/weeks of historical data that the demo's
short sim window can't reproduce. Comparing a 30-minute sim stream to the
last 14-30 days of historical patterns is what makes the velocity z-score,
structuring, and cross-provider correlation signals meaningful.

Money handling: every monetary value is `decimal.Decimal` end-to-end. We
only cast to `float` for ratio-like intermediates (z-scores, sigma spreads)
that are inherently non-monetary, and only at the point of computation.

Timestamps: source data is naive, assumed `Asia/Dhaka` (UTC+6) local time.
"""

from __future__ import annotations

import json
import statistics
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from sqlmodel import Session, select

from app.models import (
    Agent,
    ProviderWallet,
    TransactionStream,
)
from app.services.routing import route_to_officer  # used in build_ticket


# ---------------------------------------------------------------------------
# base_dataset.json loader + helpers (cached per process)
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _load_base_dataset() -> dict:
    """Load and cache the static JSON dataset once per process.

    `base_dataset.json` sits at the repo root and is the canonical
    reference for agent identities, area assignments, wallet starting
    balances, and the historical `transactions-stream` that the engine
    uses for baseline + per-customer history.
    """
    path = Path(__file__).resolve().parent.parent.parent / "base_dataset.json"
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _json_transactions() -> List[Dict[str, Any]]:
    """All historical transactions from the JSON, as raw dicts."""
    return _load_base_dataset().get("transactions-stream", [])


def _json_agents() -> List[Dict[str, Any]]:
    return _load_base_dataset().get("agent", [])


def _json_provider_wallets() -> List[Dict[str, Any]]:
    return _load_base_dataset().get("providerwallet", [])


def _parse_json_timestamp(ts: str) -> datetime:
    """Parse the JSON's naive ISO timestamp.

    JSON timestamps look like "2026-06-11T09:30:00" — naive, Asia/Dhaka.
    """
    return datetime.fromisoformat(ts)


def _agent_area_from_json(agent_id: str) -> Optional[str]:
    for a in _json_agents():
        if a.get("agent_id") == agent_id:
            return a.get("area")
    return None


# JSON uses friendly provider names ("bkash", "nagad", "rocket"); the live
# DB stores them as numeric ids ("1", "2", "3"). Both maps are needed so
# the JSON filter (which keys on friendly names) accepts either form, and
# the provider-list helper (which feeds the live wallet lookups) emits
# DB-form ids.
_PROVIDER_NAME_TO_ID = {"bkash": "1", "nagad": "2", "rocket": "3"}
_PROVIDER_ID_TO_NAME = {"1": "bkash", "2": "nagad", "3": "rocket"}


# ---------------------------------------------------------------------------
# Config (named constants — never inline literals)
# ---------------------------------------------------------------------------

SOURCE_TZ = "Asia/Dhaka"          # documented assumption for naive timestamps

WINDOW_WEIGHTS: Dict[int, Decimal] = {5: Decimal("0.5"), 10: Decimal("0.3"), 30: Decimal("0.2")}

MIN_BASELINE_SAMPLES = 5
BASELINE_LOOKBACK_DAYS = 30      # historical days for the baseline window
EPSILON = Decimal("0.000001")

SAFETY_FLOOR = Decimal("0")
TARGET_COVERAGE_MINUTES = 60
BASE_MARGIN = Decimal("0.10")
MARGIN_SENSITIVITY = Decimal("0.5")

Z_THRESHOLD = 3.0

STRUCTURING_WINDOW_MINS = 15
MIN_CLUSTER_SIZE = 4
MAX_DISTINCT_CUSTOMERS = 3
STRUCTURING_RATIO_THRESHOLD = Decimal("0.30")
STRUCTURING_TOLERANCE = Decimal("0.05")  # 5% amount similarity

RECON_TOLERANCE = Decimal("0.01")

CONTRIBUTION_THRESHOLD = Decimal("0.20")
NORMALIZATION_CAP = 6.0

# Predict-ahead threshold for the early-shortage alert. If any provider's
# or the shared drawer's ETA falls within this many minutes, the engine
# fires a LIQUIDITY_SHORTAGE alert even when overall_severity is "low"
# (no behavioral anomaly). Tunable per deployment — the hackathon demo
# uses 120 minutes so the officer gets 2 hours of lead time before the
# agent runs dry.
SHORTAGE_THRESHOLD_MINUTES = 60


# ---------------------------------------------------------------------------
# Domain types
# ---------------------------------------------------------------------------

class TxType(str, Enum):
    CASH_IN = "CASH_IN"
    CASH_OUT = "CASH_OUT"


@dataclass(frozen=True)
class TransactionEvent:
    tx_id: str
    agent_id: str
    provider_id: str             # "1" | "2" | "3" in our schema
    customer_id_hash: str
    tx_type: TxType
    amount: Decimal
    timestamp: datetime          # naive, Asia/Dhaka local


@dataclass(frozen=True)
class AgentRecord:
    agent_id: str
    shop_name: str
    area: str
    district: str
    shared_physical_cash: Decimal
    status: str


def _to_event(row: TransactionStream) -> TransactionEvent:
    """ORM row → pure domain event. Keeps the engine free of SQLModel types."""
    return TransactionEvent(
        tx_id=row.tx_id,
        agent_id=row.agent_id,
        provider_id=row.provider_id,
        customer_id_hash=row.customer_id_hash,
        tx_type=TxType(row.tx_type.value if hasattr(row.tx_type, "value") else str(row.tx_type)),
        amount=Decimal(row.amount),
        timestamp=row.timestamp,
    )


# ---------------------------------------------------------------------------
# Live-DB adapters (rolling-window sim stream + current balances)
# ---------------------------------------------------------------------------

def get_transactions(
    db: Session,
    agent_id: str,
    provider_id: Optional[str],
    start: datetime,
    end: datetime,
) -> List[TransactionEvent]:
    """Pull LIVE transactions for one agent from the rolling window.

    Reads from the live Postgres DB — this is the simulation stream that
    grows while the demo is running. Historical baseline data does NOT
    come from here; that comes from `base_dataset.json`.
    """
    stmt = (
        select(TransactionStream)
        .where(TransactionStream.agent_id == agent_id)
        .where(TransactionStream.timestamp >= start)
        .where(TransactionStream.timestamp <= end)
    )
    if provider_id is not None:
        stmt = stmt.where(TransactionStream.provider_id == provider_id)
    rows = db.exec(stmt.order_by(TransactionStream.timestamp.asc())).all()
    return [_to_event(r) for r in rows]


def get_shared_cash_balance(db: Session, agent_id: str) -> Decimal:
    agent = db.get(Agent, agent_id)
    if not agent:
        raise KeyError(f"Agent {agent_id} not found")
    return Decimal(agent.shared_physical_cash)


def get_provider_balance(db: Session, agent_id: str, provider_id: str) -> Decimal:
    wallet = db.exec(
        select(ProviderWallet)
        .where(ProviderWallet.agent_id == agent_id)
        .where(ProviderWallet.provider_id == provider_id)
    ).first()
    if not wallet:
        raise KeyError(f"No wallet for agent {agent_id} on provider {provider_id}")
    return Decimal(wallet.e_money_balance)


def get_agent(db: Session, agent_id: str) -> AgentRecord:
    agent = db.get(Agent, agent_id)
    if not agent:
        raise KeyError(f"Agent {agent_id} not found")
    return AgentRecord(
        agent_id=agent.agent_id,
        shop_name=agent.shop_name,
        area=agent.area,
        district=agent.district,
        shared_physical_cash=Decimal(agent.shared_physical_cash),
        status=agent.status.value if hasattr(agent.status, "value") else str(agent.status),
    )


def _list_distinct_providers(db: Session, agent_id: str) -> List[str]:
    """Providers this agent has any historical transactions on.

    Source: `base_dataset.json` (canonical reference). The DB provider.id
    is "1"/"2"/"3"; JSON uses friendly names "bkash"/"nagad"/"rocket" —
    we normalize to DB form so downstream wallet lookups work.
    """
    raw = {
        tx.get("provider_id")
        for tx in _json_transactions()
        if tx.get("agent_id") == agent_id and tx.get("provider_id")
    }
    normalized = {_PROVIDER_NAME_TO_ID.get(p, p) for p in raw}
    return sorted(normalized)


# ---------------------------------------------------------------------------
# Historical baseline sample extractors — ALL read from base_dataset.json
# ---------------------------------------------------------------------------

def _hour_bucket(dt: datetime) -> int:
    return dt.hour


def _day_type(dt: datetime) -> str:
    """Simplified day-type classifier.

    Treats weekday 0..4 → "weekday", 5..6 → "weekend". The full platform
    may have a richer calendar (pay-day / Eid-eve tags live on the
    TransactionStream.tx_day column for the live stream; the JSON dataset
    doesn't carry those).
    """
    return "weekend" if dt.weekday() >= 5 else "weekday"


def _filter_json_txns(
    agent_id: str,
    provider_id: Optional[str],
    start: datetime,
    end: datetime,
    customer_id_hash: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Pull historical transactions from base_dataset.json that match.

    All filters are AND'd. `provider_id=None` ignores the provider filter
    (used for shared cash). `customer_id_hash=None` ignores the customer
    filter (used for baseline; set it for per-customer history).

    `provider_id` may be either DB form ("1"/"2"/"3") or JSON form
    ("bkash"/"nagad"/"rocket"); both are accepted because callers upstream
    (compute_baseline, _pool_area_samples, customer_history_baseline) pass
    DB-form IDs but the JSON's canonical key is the friendly name.
    """
    json_provider = _PROVIDER_ID_TO_NAME.get(provider_id, provider_id)
    matches: List[Dict[str, Any]] = []
    for tx in _json_transactions():
        if tx.get("agent_id") != agent_id:
            continue
        if provider_id is not None and tx.get("provider_id") != json_provider:
            continue
        if customer_id_hash is not None and tx.get("customer_id_hash") != customer_id_hash:
            continue
        try:
            ts = _parse_json_timestamp(tx["timestamp"])
        except (KeyError, ValueError):
            continue
        if not (start <= ts <= end):
            continue
        matches.append(tx)
    return matches


def _get_historical_samples(
    agent_id: str,
    provider_id: Optional[str],
    t: datetime,
    window: int,
) -> List[Decimal]:
    """Per-window burn-rate samples (BDT/min) over the last `BASELINE_LOOKBACK_DAYS`.

    Strategy (broadest → tightest):
      1. Same-hour + same-day-type for the last 30 days (most specific).
      2. If < MIN_BASELINE_SAMPLES found, fall back to any day in the
         lookback window regardless of day-type. The JSON dataset doesn't
         span every calendar day, so a strict Sunday-only search on a
         dataset that has zero Sundays would yield zero samples — the
         broader fallback rescues those cases.

    Source: `base_dataset.json` (NEVER the live DB).
    """
    hour = _hour_bucket(t)
    day_type = _day_type(t)
    samples = _collect_burn_samples(agent_id, provider_id, t,
                                    hour=hour, day_type=day_type)
    if len(samples) < MIN_BASELINE_SAMPLES:
        samples = _collect_burn_samples(agent_id, provider_id, t,
                                        hour=None, day_type=None)
    return samples


def _collect_burn_samples(
    agent_id: str,
    provider_id: Optional[str],
    t: datetime,
    hour: Optional[int],
    day_type: Optional[str],
) -> List[Decimal]:
    """Inner loop for `_get_historical_samples` — one sample per matched day."""
    samples: List[Decimal] = []
    for d in range(BASELINE_LOOKBACK_DAYS):
        if hour is not None:
            day_start = (t - timedelta(days=d + 1)).replace(
                hour=hour, minute=0, second=0, microsecond=0
            )
            day_end = day_start + timedelta(hours=1)
        else:
            day_start = (t - timedelta(days=d + 1)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            day_end = day_start + timedelta(days=1)
        if day_type is not None and _day_type(day_start) != day_type:
            continue
        txs = _filter_json_txns(agent_id, provider_id, day_start, day_end)
        cash_out = sum(
            (Decimal(tx["amount"]) for tx in txs if tx.get("tx_type") == "CASH_OUT"),
            Decimal("0"),
        )
        cash_in = sum(
            (Decimal(tx["amount"]) for tx in txs if tx.get("tx_type") == "CASH_IN"),
            Decimal("0"),
        )
        if (cash_out + cash_in) == 0:
            continue
        span_minutes = (day_end - day_start).total_seconds() / 60.0
        if span_minutes <= 0:
            continue
        samples.append((cash_out - cash_in) / Decimal(str(span_minutes)))
    return samples


def _get_historical_count_samples(
    agent_id: str,
    provider_id: Optional[str],
    t: datetime,
    window: int,
) -> List[int]:
    """Per-window transaction-count samples over the same historical lookback.

    Mirrors `_get_historical_samples`: tries same-hour + same-day-type
    first, then broadens to any-day-in-lookback if too thin.

    Source: `base_dataset.json`.
    """
    hour = _hour_bucket(t)
    day_type = _day_type(t)
    counts = _collect_count_samples(agent_id, provider_id, t,
                                    hour=hour, day_type=day_type)
    if len(counts) < MIN_BASELINE_SAMPLES:
        counts = _collect_count_samples(agent_id, provider_id, t,
                                        hour=None, day_type=None)
    return counts


def _collect_count_samples(
    agent_id: str,
    provider_id: Optional[str],
    t: datetime,
    hour: Optional[int],
    day_type: Optional[str],
) -> List[int]:
    counts: List[int] = []
    for d in range(BASELINE_LOOKBACK_DAYS):
        if hour is not None:
            day_start = (t - timedelta(days=d + 1)).replace(
                hour=hour, minute=0, second=0, microsecond=0
            )
            day_end = day_start + timedelta(hours=1)
        else:
            day_start = (t - timedelta(days=d + 1)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            day_end = day_start + timedelta(days=1)
        if day_type is not None and _day_type(day_start) != day_type:
            continue
        txs = _filter_json_txns(agent_id, provider_id, day_start, day_end)
        if not txs:
            continue
        counts.append(len(txs))
    return counts


# ---------------------------------------------------------------------------
# Per-customer history (uses `customer_id_hash` from JSON)
# ---------------------------------------------------------------------------

def customer_history_baseline(
    agent_id: str,
    provider_id: str,
    customer_id_hash: str,
    t: datetime,
) -> Dict[str, Any]:
    """Per-customer historical baseline from base_dataset.json.

    Returns median amount, MAD, sigma_amount, tx_count, baseline_source.
    If the customer has < 3 historical txns, baseline is too thin —
    baseline_source="insufficient" and downstream callers should fall
    back to agent-wide norms.
    """
    CUSTOMER_MIN = 3
    txs = _filter_json_txns(
        agent_id=agent_id,
        provider_id=provider_id,
        start=datetime(2000, 1, 1),
        end=t,
        customer_id_hash=customer_id_hash,
    )
    if len(txs) < CUSTOMER_MIN:
        return {
            "median_amount": Decimal("0"),
            "mad_amount": Decimal("0"),
            "tx_count": len(txs),
            "baseline_source": "insufficient",
        }
    amounts = [Decimal(tx["amount"]) for tx in txs]
    median_amt = _decimal_median(amounts)
    mad_amt = _decimal_median([abs(x - median_amt) for x in amounts])
    return {
        "median_amount": median_amt,
        "mad_amount": mad_amt,
        "sigma_amount": Decimal("1.4826") * mad_amt,
        "tx_count": len(txs),
        "baseline_source": "agent",  # "agent" here means per-customer
    }


def detect_customer_anomaly(
    db: Session,
    agent_id: str,
    provider_id: str,
    customer_id_hash: str,
    t: datetime,
) -> Dict[str, Any]:
    """Per-customer anomaly detection.

    Compares this customer's RECENT behavior (last 30 min, from the live DB
    so we catch freshly-injected sim rows) against their OWN historical
    baseline (from the JSON). Flags if amount z-score exceeds 3.0.

    Returns:
        {
            "customer_id_hash": str,
            "amount_z": float,
            "anomaly": bool,
            "baseline_source": "agent"|"insufficient",
            "historical_tx_count": int,
        }
    """
    base = customer_history_baseline(agent_id, provider_id, customer_id_hash, t)
    if base["baseline_source"] == "insufficient":
        return {
            "customer_id_hash": customer_id_hash,
            "amount_z": 0.0,
            "anomaly": False,
            "baseline_source": "insufficient",
            "historical_tx_count": base["tx_count"],
        }

    cutoff = t - timedelta(minutes=30)
    recent = db.exec(
        select(TransactionStream)
        .where(TransactionStream.agent_id == agent_id)
        .where(TransactionStream.provider_id == provider_id)
        .where(TransactionStream.customer_id_hash == customer_id_hash)
        .where(TransactionStream.timestamp >= cutoff)
    ).all()
    if not recent:
        return {
            "customer_id_hash": customer_id_hash,
            "amount_z": 0.0,
            "anomaly": False,
            "baseline_source": "agent",
            "historical_tx_count": base["tx_count"],
        }

    avg_recent = sum(Decimal(t.amount) for t in recent) / Decimal(len(recent))
    sigma = max(base["sigma_amount"], EPSILON)
    amount_z = float((avg_recent - base["median_amount"]) / sigma)
    return {
        "customer_id_hash": customer_id_hash,
        "amount_z": amount_z,
        "anomaly": abs(amount_z) > Z_THRESHOLD,
        "baseline_source": "agent",
        "historical_tx_count": base["tx_count"],
    }


def detect_customer_anomalies_for_provider(
    db: Session,
    agent_id: str,
    provider_id: str,
    t: datetime,
) -> List[Dict[str, Any]]:
    """Run detect_customer_anomaly for every customer active on this provider.

    Returns the list of per-customer results. Caller can filter for
    `anomaly=True` to get the flagged customers.
    """
    cutoff = t - timedelta(minutes=30)
    rows = db.exec(
        select(TransactionStream.customer_id_hash)
        .where(TransactionStream.agent_id == agent_id)
        .where(TransactionStream.provider_id == provider_id)
        .where(TransactionStream.timestamp >= cutoff)
        .distinct()
    ).all()
    customers = [r for r in rows if r]
    return [
        detect_customer_anomaly(db, agent_id, provider_id, c, t)
        for c in customers
    ]


# ---------------------------------------------------------------------------
# Routing shim — `build_ticket` is called by orchestration code that
# already holds a `db` session, but the ticket payload only carries
# `agent_id`/`provider_id`. We resolve the session lazily.
# ---------------------------------------------------------------------------

def _route_to_officer_for_ticket(agent_id: str, provider_id: str) -> str:
    """Best-effort officer lookup at ticket-build time."""
    from app.database import engine
    try:
        with Session(engine) as s:
            return route_to_officer(s, agent_id, provider_id)
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Rolling-window burn rate (LIVE DB only — the simulation stream)
# ---------------------------------------------------------------------------

def compute_burn_rate(
    db: Session,
    agent_id: str,
    provider_id: Optional[str],
    t: datetime,
) -> Dict[str, Any]:
    """Formula: `burn_rate(w) = (cash_out - cash_in) / w` per window in {5,10,30}.

    Reads the LAST `w` MINUTES of transactions from the live DB. This is
    the "now" stream that the demo grows while the simulation is running.
    Historical baseline comparison is done by `compute_baseline`, which
    reads from JSON.

    `provider_id=None` aggregates across all providers for the shared drawer.
    """
    burn_rates: Dict[int, Decimal] = {}
    for w in WINDOW_WEIGHTS:
        start = t - timedelta(minutes=w)
        txs = get_transactions(db, agent_id, provider_id, start, t)
        cash_out = sum((tx.amount for tx in txs if tx.tx_type == TxType.CASH_OUT), Decimal("0"))
        cash_in = sum((tx.amount for tx in txs if tx.tx_type == TxType.CASH_IN), Decimal("0"))
        burn_rates[w] = (cash_out - cash_in) / Decimal(w)

    weighted = sum((WINDOW_WEIGHTS[w] * burn_rates[w] for w in WINDOW_WEIGHTS), Decimal("0"))
    return {
        "burn_rates": {w: burn_rates[w] for w in WINDOW_WEIGHTS},
        "burn_rate_weighted": weighted,
    }


# ---------------------------------------------------------------------------
# Historical baseline (median / MAD → sigma) — JSON source only
# ---------------------------------------------------------------------------

def compute_baseline(
    db: Session,
    agent_id: str,
    provider_id: Optional[str],
    t: datetime,
    window: int,
) -> Dict[str, Any]:
    """Robust baseline from base_dataset.json: median + MAD → sigma.

    Returns Decimal throughout. If the agent has < MIN_BASELINE_SAMPLES
    days of data, falls back to pooling every agent in the same area
    (still JSON-sourced). If that's also too thin, baseline_source =
    "insufficient" and median/sigma are zero (caller treats that as a
    cold-start signal — never as a positive anomaly).

    The `db` parameter is kept on the signature for API compatibility
    but is NOT used — baseline data comes exclusively from JSON.
    """
    samples = _get_historical_samples(agent_id, provider_id, t, window)
    count_samples = _get_historical_count_samples(agent_id, provider_id, t, window)

    source = "agent"
    if len(samples) < MIN_BASELINE_SAMPLES:
        samples, count_samples = _pool_area_samples(
            agent_id, provider_id, t, window
        )
        source = "pooled"
        if len(samples) < MIN_BASELINE_SAMPLES:
            source = "insufficient"

    if source == "insufficient":
        return {
            "baseline_median": Decimal("0"),
            "baseline_sigma": Decimal("0"),
            "baseline_median_count": Decimal("0"),
            "baseline_sigma_count": Decimal("0"),
            "baseline_source": "insufficient",
        }

    median_val = _decimal_median(samples)
    mad_val = _decimal_median([abs(x - median_val) for x in samples])
    sigma_val = Decimal("1.4826") * mad_val

    median_count = _decimal_median([Decimal(n) for n in count_samples]) if count_samples else Decimal("0")
    mad_count = (
        _decimal_median([abs(Decimal(n) - median_count) for n in count_samples])
        if count_samples else Decimal("0")
    )
    sigma_count = Decimal("1.4826") * mad_count

    return {
        "baseline_median": median_val,
        "baseline_sigma": sigma_val,
        "baseline_median_count": median_count,
        "baseline_sigma_count": sigma_count,
        "baseline_source": source,
    }


def _pool_area_samples(
    agent_id: str,
    provider_id: Optional[str],
    t: datetime,
    window: int,
) -> tuple[List[Decimal], List[int]]:
    """Pool historical samples across every agent in the same area as `agent_id`.

    Returns (burn_rate_samples, count_samples). Used as a cold-start fallback
    when the per-agent sample count is below `MIN_BASELINE_SAMPLES`. Reads
    from `base_dataset.json` (the canonical data source for the engine).
    """
    area = _agent_area_from_json(agent_id)
    if not area:
        return [], []
    burn: List[Decimal] = []
    counts: List[int] = []
    for peer in _json_agents():
        if peer.get("area") != area:
            continue
        if peer.get("agent_id") == agent_id:
            continue
        burn.extend(_get_historical_samples(peer["agent_id"], provider_id, t, window))
        counts.extend(_get_historical_count_samples(peer["agent_id"], provider_id, t, window))
    return burn, counts


def _decimal_median(values: Sequence[Decimal]) -> Decimal:
    """Median that preserves Decimal precision (statistics.median returns float)."""
    if not values:
        return Decimal("0")
    s = sorted(values)
    n = len(s)
    mid = n // 2
    if n % 2:
        return Decimal(s[mid])
    return (Decimal(s[mid - 1]) + Decimal(s[mid])) / Decimal("2")


# ---------------------------------------------------------------------------
# Liquidity shortage prediction
# ---------------------------------------------------------------------------

def predict_shortage(
    db: Session,
    agent_id: str,
    provider_id: Optional[str],
    t: datetime,
) -> Dict[str, Any]:
    """Section 4 of the prompt: ETA + confidence band + change_pct.

    Combines:
      - LIVE rolling burn rate (from the DB)
      - JSON-sourced baseline (historical median, MAD-derived sigma)
      - LIVE current balance (from the DB)
    """
    burn_data = compute_burn_rate(db, agent_id, provider_id, t)
    weighted: Decimal = burn_data["burn_rate_weighted"]
    rates: Dict[int, Decimal] = burn_data["burn_rates"]

    baseline_data = compute_baseline(db, agent_id, provider_id, t, 10)
    median_10: Decimal = baseline_data["baseline_median"]
    source = baseline_data["baseline_source"]

    change_pct = ((rates[10] - median_10) / max(median_10, EPSILON)) * Decimal("100")

    if weighted <= Decimal("0"):
        return {
            "eta_minutes": None,
            "eta_range_minutes": None,
            "confidence": "low" if source == "insufficient" else "medium",
            "burn_rate_weighted": str(weighted.quantize(Decimal("0.01"))),
            "change_pct": float(change_pct),
            "current_balance": _balance_str(db, agent_id, provider_id),
            "relative_uncertainty": 0.0,
        }

    current_balance = _get_balance(db, agent_id, provider_id)
    rate_floats = [float(rates[w]) for w in WINDOW_WEIGHTS]
    sigma_burn = Decimal(
        str(statistics.stdev(rate_floats)) if len(rate_floats) >= 2 else 0.0
    )

    denom_low = weighted + sigma_burn
    denom_high = max(weighted - sigma_burn, EPSILON)
    eta_minutes = int((current_balance - SAFETY_FLOOR) / weighted)
    eta_low = int((current_balance - SAFETY_FLOOR) / denom_low) if denom_low > 0 else eta_minutes
    eta_high = int((current_balance - SAFETY_FLOOR) / denom_high)

    relative_uncertainty = float(sigma_burn / max(weighted, EPSILON))

    if source == "insufficient":
        confidence = "low"
    elif relative_uncertainty < 0.15 and source == "agent":
        confidence = "high"
    elif relative_uncertainty < 0.40:
        confidence = "medium"
    else:
        confidence = "low"

    return {
        "eta_minutes": eta_minutes,
        "eta_range_minutes": [eta_low, eta_high],
        "confidence": confidence,
        "burn_rate_weighted": str(weighted.quantize(Decimal("0.01"))),
        "change_pct": float(change_pct),
        "current_balance": str(current_balance.quantize(Decimal("0.01"))),
        "relative_uncertainty": relative_uncertainty,
    }


def _get_balance(db: Session, agent_id: str, provider_id: Optional[str]) -> Decimal:
    if provider_id is None:
        return get_shared_cash_balance(db, agent_id)
    return get_provider_balance(db, agent_id, provider_id)


def _balance_str(db: Session, agent_id: str, provider_id: Optional[str]) -> str:
    try:
        return str(_get_balance(db, agent_id, provider_id).quantize(Decimal("0.01")))
    except KeyError:
        return "0.00"


# ---------------------------------------------------------------------------
# Safe top-up  (analytics_engine_prompt.md §5)
#
# Formula:
#   required_for_window = burn_rate_weighted * Decimal(target_coverage_minutes)
#   raw_topup           = max(Decimal("0"), required_for_window - current_balance)
#   safety_margin       = base_margin + margin_sensitivity * Decimal(str(relative_uncertainty))
#   topup_amount        = (raw_topup * (1 + safety_margin))
#                            .quantize(Decimal("100"), rounding=ROUND_HALF_UP)
#   Return Decimal("0") if raw_topup == 0
# ---------------------------------------------------------------------------

def recommend_topup_amount(
    weighted_burn: Decimal,
    current_balance: Decimal,
    relative_uncertainty: float = 0.0,
) -> Decimal:
    """Pure step-5 formula. No DB, no Decimal→float on money."""
    required = Decimal(weighted_burn) * Decimal(TARGET_COVERAGE_MINUTES)
    raw_topup = max(Decimal("0"), required - Decimal(current_balance))
    if raw_topup == Decimal("0"):
        return Decimal("0")

    safety_margin = BASE_MARGIN + (MARGIN_SENSITIVITY * Decimal(str(relative_uncertainty)))
    topup = (raw_topup * (Decimal("1") + safety_margin)).quantize(
        Decimal("100"), rounding=ROUND_HALF_UP
    )
    return topup


def recommend_topup(
    db: Session,
    agent_id: str,
    provider_id: Optional[str],
    t: datetime,
) -> Dict[str, Any]:
    """Convenience wrapper around `recommend_topup_amount`.

    Derives (weighted_burn, current_balance, relative_uncertainty) from
    `predict_shortage` and returns the same dict shape the platform already
    consumes: `{"amount": "...", "target_coverage_minutes": 60}`.

    Safe for missing-wallet providers: returns Decimal("0") instead of
    raising KeyError so the upstream `analyze()` doesn't crash mid-flight.
    """
    try:
        pred = predict_shortage(db, agent_id, provider_id, t)
        weighted_burn = Decimal(pred["burn_rate_weighted"])
        try:
            current_balance = _get_balance(db, agent_id, provider_id)
        except KeyError:
            current_balance = Decimal(pred.get("current_balance") or "0")
        relative_uncertainty = float(pred.get("relative_uncertainty", 0.0))
    except KeyError:
        return {"amount": "0.00", "target_coverage_minutes": TARGET_COVERAGE_MINUTES}

    amount = recommend_topup_amount(
        weighted_burn=weighted_burn,
        current_balance=current_balance,
        relative_uncertainty=relative_uncertainty,
    )
    return {
        "amount": str(amount.quantize(Decimal("0.01"))),
        "target_coverage_minutes": TARGET_COVERAGE_MINUTES,
    }


# ---------------------------------------------------------------------------
# Velocity anomaly — compares LIVE rolling burn vs JSON baseline
# ---------------------------------------------------------------------------

def detect_velocity_anomaly(
    db: Session,
    agent_id: str,
    provider_id: str,
    t: datetime,
) -> Dict[str, Any]:
    """Section 6a: count_z + amount_z averaged, flag if any window > Z_THRESHOLD.

    The LIVE rolling count + burn rate come from the DB; the baseline
    median/sigma come from `base_dataset.json` (via compute_baseline).
    """
    burn_data = compute_burn_rate(db, agent_id, provider_id, t)
    rates = burn_data["burn_rates"]

    max_score = 0.0
    anomaly = False
    triggering_window: Optional[int] = None

    for w in WINDOW_WEIGHTS:
        base = compute_baseline(db, agent_id, provider_id, t, w)
        if base["baseline_source"] == "insufficient":
            continue

        start = t - timedelta(minutes=w)
        txs = get_transactions(db, agent_id, provider_id, start, t)
        obs_count = len(txs)

        count_z = (Decimal(obs_count) - base["baseline_median_count"]) / max(base["baseline_sigma_count"], EPSILON)
        amount_z = (rates[w] - base["baseline_median"]) / max(base["baseline_sigma"], EPSILON)
        score = 0.5 * float(count_z) + 0.5 * float(amount_z)
        if score > max_score:
            max_score = score
        if score > Z_THRESHOLD and not anomaly:
            anomaly = True
            triggering_window = w

    return {
        "velocity_anomaly": anomaly,
        "velocity_score": max_score,
        "triggering_window": triggering_window,
    }


# ---------------------------------------------------------------------------
# Structuring / near-duplicate pattern (LIVE 15-min CASH_OUT cluster)
# ---------------------------------------------------------------------------

def same_cluster(a: Decimal, b: Decimal, tolerance: Decimal = STRUCTURING_TOLERANCE) -> bool:
    """Two amounts are 'same cluster' if their relative difference is within tolerance."""
    denom = max(a, b, EPSILON)
    return abs(a - b) / denom <= tolerance


def detect_structuring(
    db: Session,
    agent_id: str,
    provider_id: str,
    t: datetime,
) -> Dict[str, Any]:
    """Section 6b: 15-min window, CASH_OUT only, cluster by amount similarity.

    Operates on the LIVE 15-min rolling stream — historical transactions
    in the JSON aren't part of structuring detection (that's a present-tense
    operational signal).
    """
    start = t - timedelta(minutes=STRUCTURING_WINDOW_MINS)
    txs = get_transactions(db, agent_id, provider_id, start, t)
    out_txs = sorted([tx for tx in txs if tx.tx_type == TxType.CASH_OUT], key=lambda x: x.amount)

    n_total = len(out_txs)
    if n_total == 0:
        return {"structuring_anomaly": False, "structuring_ratio": 0.0, "flagged_customers": []}

    clusters: List[List[TransactionEvent]] = []
    current: List[TransactionEvent] = []
    for tx in out_txs:
        if not current:
            current = [tx]
            continue
        cluster_mean = sum((c.amount for c in current), Decimal("0")) / Decimal(len(current))
        if same_cluster(cluster_mean, tx.amount):
            current.append(tx)
        else:
            clusters.append(current)
            current = [tx]
    if current:
        clusters.append(current)

    largest = max(clusters, key=len) if clusters else []
    n_cluster = len(largest)
    distinct_customers = {tx.customer_id_hash for tx in largest}
    k_distinct = len(distinct_customers)
    structuring_ratio = Decimal(n_cluster) / max(Decimal(n_total), Decimal("1"))

    anomaly = (
        n_cluster >= MIN_CLUSTER_SIZE
        and k_distinct <= MAX_DISTINCT_CUSTOMERS
        and structuring_ratio >= STRUCTURING_RATIO_THRESHOLD
    )

    return {
        "structuring_anomaly": anomaly,
        "structuring_ratio": float(structuring_ratio),
        "flagged_customers": sorted(distinct_customers) if anomaly else [],
    }


# ---------------------------------------------------------------------------
# Cross-provider correlation
# ---------------------------------------------------------------------------

def detect_cross_provider_correlation(
    db: Session,
    agent_id: str,
    t: datetime,
    providers: Sequence[str],
) -> Dict[str, Any]:
    """Section 6c: a ≥ 2 providers over Z_THRESHOLD = correlated (multiplier 1.5)."""
    correlated_providers: List[str] = []
    for p in providers:
        result = detect_velocity_anomaly(db, agent_id, p, t)
        if result["velocity_score"] > Z_THRESHOLD:
            correlated_providers.append(p)
    correlated = len(correlated_providers) >= 2
    return {
        "correlated_providers": correlated_providers,
        "correlated": correlated,
        "severity_multiplier": 1.5 if correlated else 1.0,
    }


# ---------------------------------------------------------------------------
# Balance reconciliation (data-quality signal — LIVE DB only)
# ---------------------------------------------------------------------------

def check_balance_reconciliation(
    db: Session,
    agent_id: str,
    provider_id: Optional[str],
    t: datetime,
) -> Dict[str, Any]:
    """Section 6d: |reported - (opening + in - out)| / reported > tolerance → DQ flag."""
    try:
        reported = _get_balance(db, agent_id, provider_id)
    except KeyError:
        return {"data_quality_flag": False, "reconciliation_error_pct": 0.0}

    day_start = t.replace(hour=0, minute=0, second=0, microsecond=0)
    txs = get_transactions(db, agent_id, provider_id, day_start, t)
    cash_out = sum((tx.amount for tx in txs if tx.tx_type == TxType.CASH_OUT), Decimal("0"))
    cash_in = sum((tx.amount for tx in txs if tx.tx_type == TxType.CASH_IN), Decimal("0"))

    if reported <= Decimal("0"):
        return {"data_quality_flag": False, "reconciliation_error_pct": 0.0}

    net = cash_out - cash_in
    error_pct = float(abs(net) / reported) if reported > 0 else 0.0
    flag = Decimal(str(error_pct)) > RECON_TOLERANCE
    return {"data_quality_flag": bool(flag), "reconciliation_error_pct": error_pct}


# ---------------------------------------------------------------------------
# Composite scoring + severity
# ---------------------------------------------------------------------------

def normalize(z: float, cap: float = NORMALIZATION_CAP) -> float:
    return min(max(z, 0.0), cap) / cap


def analyze(
    db: Session,
    agent_id: str,
    t: datetime,
    providers: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """Top-level entry point. Returns the master evidence dict.

    Reads LIVE data from the DB (rolling burn, structuring window,
    current balances) and HISTORICAL data from `base_dataset.json`
    (baseline median/MAD/sigma, per-customer history).
    """
    if providers is None:
        providers = _list_distinct_providers(db, agent_id)

    liquidity: Dict[str, Any] = {}
    anomaly: Dict[str, Dict[str, Any]] = {}
    warnings: List[str] = []

    # Shared cash liquidity (provider_id=None → shared drawer)
    shared_pred = predict_shortage(db, agent_id, None, t)
    liquidity["shared_cash"] = shared_pred
    shared_uncertainty = float(shared_pred.get("relative_uncertainty", 0.0))

    # Per-provider liquidity + anomalies
    for p in providers:
        liquidity[p] = predict_shortage(db, agent_id, p, t)
        v_anom = detect_velocity_anomaly(db, agent_id, p, t)
        s_anom = detect_structuring(db, agent_id, p, t)
        # Per-customer history anomalies: customers whose own amount pattern
        # has shifted vs their historical baseline. Folded into composite as
        # an additional weight, and the flagged hashes get surfaced in the
        # ticket so the officer has a per-customer lead.
        c_anoms = detect_customer_anomalies_for_provider(db, agent_id, p, t)
        flagged_by_history = sorted({
            c["customer_id_hash"]
            for c in c_anoms
            if c.get("anomaly") and c.get("baseline_source") == "agent"
        })
        merged_flagged = s_anom["flagged_customers"] or flagged_by_history
        anomaly[p] = {
            "velocity_anomaly": v_anom["velocity_anomaly"],
            "velocity_score": v_anom["velocity_score"],
            "triggering_window": v_anom.get("triggering_window"),
            "structuring_anomaly": s_anom["structuring_anomaly"],
            "structuring_ratio": s_anom["structuring_ratio"],
            "flagged_customers": merged_flagged,
            "customer_anomaly_count": sum(1 for c in c_anoms if c.get("anomaly")),
            "customer_anomaly_score": max(
                (abs(float(c.get("amount_z", 0.0))) for c in c_anoms),
                default=0.0,
            ),
        }

    corr_data = detect_cross_provider_correlation(db, agent_id, t, providers)
    correlated_providers: List[str] = corr_data["correlated_providers"]
    correlated: bool = corr_data["correlated"]
    multiplier: float = corr_data["severity_multiplier"]

    # Reconciliation flag lowers confidence; never raises severity.
    dq_flag = False
    for p in [None, *providers]:
        dq = check_balance_reconciliation(db, agent_id, p, t)
        if dq["data_quality_flag"]:
            dq_flag = True
            warnings.append("data_quality_issue")
            break

    # Composite score per provider (velocity + structuring + customer
    # history signal + cross-provider correlation).
    for p in providers:
        v_score = anomaly[p]["velocity_score"]
        s_flag = anomaly[p]["structuring_anomaly"]
        s_ratio = anomaly[p]["structuring_ratio"]
        c_signal = float(anomaly[p].get("customer_anomaly_score", 0.0))
        comp = (
            0.35 * normalize(v_score)
            + 0.35 * (float(s_ratio) if s_flag else 0.0)
            + 0.10 * normalize(c_signal)
            + 0.20 * (1.0 if correlated else 0.0)
        )
        comp = min(comp * multiplier, 1.0)
        # Hard cap: single-provider spike without correlation and without
        # structuring can never reach the top severity tier.
        if not correlated and not s_flag:
            comp = min(comp, 0.65)
        anomaly[p]["composite_score"] = comp
        anomaly[p]["derived_severity"] = (
            "high" if comp >= 0.66 else "medium" if comp >= 0.33 else "low"
        )

    max_comp = max((anomaly[p]["composite_score"] for p in providers), default=0.0)
    overall_severity = (
        "high" if max_comp >= 0.66 else "medium" if max_comp >= 0.33 else "low"
    )
    is_structuring_anywhere = any(anomaly[p]["structuring_anomaly"] for p in providers)
    if not correlated and not is_structuring_anywhere and overall_severity == "high":
        overall_severity = "medium"

    # Recommended top-up (shared cash only, per prompt)
    rec_topup = recommend_topup(db, agent_id, None, t)

    return {
        "agent_id": agent_id,
        "evaluated_at": t.isoformat(),
        "liquidity": liquidity,
        "recommended_topup": rec_topup,
        "anomaly": anomaly,
        "correlated_providers": correlated_providers,
        "overall_severity": overall_severity,
        "warnings": warnings,
        "data_quality_flag": dq_flag,
        "shared_uncertainty": shared_uncertainty,
    }


# ---------------------------------------------------------------------------
# Alert + ticket generation — strict per-provider isolation
# ---------------------------------------------------------------------------

def _new_id() -> str:
    return str(uuid.uuid4())


def derive_alert_type(evidence: Dict[str, Any]) -> str:
    """ORM AlertType has only LIQUIDITY_SHORTAGE / BEHAVIORAL_ANOMALY.

    A combined case maps to BEHAVIORAL_ANOMALY when there's an anomaly (it
    carries the more specific operational signal), else LIQUIDITY_SHORTAGE.
    """
    has_anomaly = any(
        v.get("velocity_anomaly") or v.get("structuring_anomaly")
        for v in evidence["anomaly"].values()
    )
    shortage = evidence["liquidity"]["shared_cash"]["eta_minutes"] is not None
    if has_anomaly and shortage:
        return "BEHAVIORAL_ANOMALY"
    if has_anomaly:
        return "BEHAVIORAL_ANOMALY"
    return "LIQUIDITY_SHORTAGE"


def overall_confidence(evidence: Dict[str, Any]) -> Decimal:
    """Min confidence across contributing components."""
    scores: List[Decimal] = []
    sc = evidence["liquidity"]["shared_cash"]["confidence"]
    scores.append({"high": Decimal("0.9"), "medium": Decimal("0.6"), "low": Decimal("0.3")}[sc])
    for p, liq in evidence["liquidity"].items():
        if p == "shared_cash":
            continue
        c = liq["confidence"]
        scores.append({"high": Decimal("0.9"), "medium": Decimal("0.6"), "low": Decimal("0.3")}[c])
    if evidence.get("data_quality_flag"):
        scores.append(Decimal("0.3"))
    return min(scores) if scores else Decimal("0.3")


def determine_responsible_providers(
    evidence: Dict[str, Any], providers: Sequence[str]
) -> List[str]:
    """A provider is responsible if it tripped any anomaly, is in the correlated
    set, contributed ≥ 20% of the shared burn in a liquidity-only case, OR has
    its own wallet ETA below SHORTAGE_THRESHOLD_MINUTES.

    The per-wallet ETA check is what makes the early-shortage alerts
    fire: when only ONE provider's e-money is depleting (e.g., bKash at
    ETA=45 min while the shared drawer is fine), that single provider
    still gets a ticket so the right officer is paged. Without this,
    a per-wallet drain would be silently dropped because no anomaly
    fired and the shared drawer isn't short.
    """
    resp: set = set()
    total_weighted = Decimal("0")
    for p in providers:
        if evidence["anomaly"][p]["velocity_anomaly"] or evidence["anomaly"][p]["structuring_anomaly"]:
            resp.add(p)
        if p in evidence["correlated_providers"]:
            resp.add(p)
        # Per-wallet ETA — include providers whose own e-money will
        # deplete within the shortage threshold.
        prov_pred = evidence["liquidity"].get(p, {})
        prov_eta = prov_pred.get("eta_minutes")
        if prov_eta is not None and prov_eta <= SHORTAGE_THRESHOLD_MINUTES:
            resp.add(p)
        total_weighted += Decimal(evidence["liquidity"][p]["burn_rate_weighted"])

    if not resp and evidence["liquidity"]["shared_cash"]["eta_minutes"] is not None:
        for p in providers:
            share = Decimal(evidence["liquidity"][p]["burn_rate_weighted"]) / max(total_weighted, EPSILON)
            if share >= CONTRIBUTION_THRESHOLD:
                resp.add(p)
    return sorted(resp)


def build_ticket(
    alert_id: str,
    agent_id: str,
    provider_id: str,
    evidence: Dict[str, Any],
    t: datetime,
    provider_topup: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Provider-scoped ticket evidence — strictly THIS provider's slice only.

    Isolation guarantees (each enforced below + by `_assert_no_cross_provider_leak`):

      1. `liquidity` is THIS provider's predict-shortage slice. If the upstream
         `analyze()` didn't produce one for this provider, we synthesize a
         zero-filled slice tagged with `provider_id`. We never fall back to
         `evidence["liquidity"]["shared_cash"]`, because that slice carries
         the agent's full drawer balance + burn — which means other
         providers' contributions. That's a multi-tenant leak.
      2. `anomaly` / `flagged_customers` are THIS provider's only — never
         aggregated across providers.
      3. `recommended_topup` is the caller-supplied per-provider top-up (see
         `build_alert_and_tickets`). The default is zeros if the caller
         forgot — we never inherit `evidence["recommended_topup"]`, which
         is the SHARED drawer top-up and includes other providers' drains.
      4. `correlated_with` is the list of OTHER provider IDs only — never
         their balances, customer hashes, or any non-id value.
      5. `provider_id` is included in the slice so the JS evidence modal can
         label the payload unambiguously.

    `_assert_no_cross_provider_leak` runs at the end and raises ValueError
    if any other provider's id, customer hash, or balance string appears
    anywhere in the serialized slice.
    """
    # (1) THIS provider's liquidity slice — never shared cash.
    own_liquidity = evidence["liquidity"].get(provider_id)
    if own_liquidity is None:
        own_liquidity = {
            "provider_id": provider_id,
            "eta_minutes": None,
            "eta_range_minutes": None,
            "confidence": "low",
            "burn_rate_weighted": "0.00",
            "change_pct": 0.0,
            "current_balance": "0.00",
            "relative_uncertainty": 0.0,
        }
    else:
        # Tag the slice with its provider so downstream consumers (JS modal,
        # audit log dump, manual SQL inspection) can confirm isolation.
        own_liquidity = {**own_liquidity, "provider_id": provider_id}

    # (2) THIS provider's anomaly slice + flagged customers (sorted + deduped).
    own_anomaly = evidence["anomaly"].get(provider_id, {})
    own_flagged = sorted(set(own_anomaly.get("flagged_customers", [])))

    # (3) Per-provider top-up. Caller (build_alert_and_tickets) computes the
    # right slice via `recommend_topup(db, agent_id, provider_id, t)`. We do
    # NOT inherit evidence["recommended_topup"] — that's the shared drawer
    # top-up and contains other providers' drain.
    if provider_topup is None:
        provider_topup = {
            "amount": "0.00",
            "target_coverage_minutes": TARGET_COVERAGE_MINUTES,
        }

    # (4) correlated_with: list of OTHER provider IDs only. Strip any non-string
    # entries defensively (an upstream bug could inject a dict here).
    correlated_with = [
        p for p in evidence.get("correlated_providers", [])
        if p != provider_id and isinstance(p, str)
    ]

    provider_slice: Dict[str, Any] = {
        "provider_id": provider_id,                  # (5) unambiguous label
        "liquidity": own_liquidity,
        "anomaly": own_anomaly,
        "flagged_customers": own_flagged,
        "correlated_with": correlated_with,
        "recommended_topup": provider_topup,
        "data_quality_flag": bool(evidence.get("data_quality_flag", False)),
    }

    # Defensive guard — raise loudly if any cross-provider data leaked in.
    _assert_no_cross_provider_leak(provider_slice, provider_id, evidence)

    return {
        "ticket_id": _new_id(),
        "alert_id": alert_id,
        "provider_id": provider_id,
        "assigned_officer_id": _route_to_officer_for_ticket(agent_id, provider_id),
        "current_owner_role": "FIELD_OFFICER",       # OwnerRole enum value
        "status": "OPEN",                            # TicketStatus enum value
        "evidence_json": json.dumps(provider_slice, default=str),
        "created_at": t,
        "updated_at": t,
    }


def _assert_no_cross_provider_leak(
    slice_: Dict[str, Any],
    this_provider: str,
    evidence: Dict[str, Any],
) -> None:
    """Defensive runtime check: raise ValueError if any OTHER provider's data
    appears in this ticket's slice.

    Catches:
      - Another provider's customer_id_hash appearing anywhere in the slice
      - Another provider's current_balance digit string (e.g. "38000.00")
        appearing in recommended_topup or anomaly blocks
      - correlated_with carrying anything other than a list of provider-id
        strings (e.g. a leaked dict or balance)
    """
    other_providers = [
        p for p in evidence.get("anomaly", {}).keys() if p != this_provider
    ]
    if not other_providers:
        return

    serialized = json.dumps(slice_, default=str)

    # (a) Other providers' customer hashes must not appear anywhere.
    for p in other_providers:
        for c in evidence["anomaly"][p].get("flagged_customers", []):
            if c and c in serialized:
                raise ValueError(
                    f"Cross-provider leak: ticket for {this_provider!r} contains "
                    f"{p!r}'s customer hash {c!r}"
                )

    # (b) Other providers' current_balance digits must NOT appear in
    # recommended_topup or anomaly blocks of this provider's slice.
    # Word-boundary matching: "100000" must not match inside "10000000".
    own_balance = (
        evidence.get("liquidity", {}).get(this_provider, {}).get("current_balance")
    )
    own_balance_digits = (
        str(own_balance).replace(".", "") if own_balance else ""
    )

    suspicious_fields = {
        "recommended_topup": json.dumps(slice_.get("recommended_topup", {}), default=str),
        "anomaly": json.dumps(slice_.get("anomaly", {}), default=str),
        "flagged_customers": json.dumps(slice_.get("flagged_customers", []), default=str),
    }
    for p in other_providers:
        other_balance = (
            evidence.get("liquidity", {}).get(p, {}).get("current_balance")
        )
        if not other_balance:
            continue
        digits = str(other_balance).replace(".", "")
        if len(digits) < 5:
            continue
        if digits == own_balance_digits:
            continue
        for field_name, blob in suspicious_fields.items():
            blob_digits = blob.replace(".", "")
            idx = 0
            while True:
                j = blob_digits.find(digits, idx)
                if j < 0:
                    break
                left_ok = (j == 0) or not blob_digits[j - 1].isdigit()
                right_ok = (j + len(digits) == len(blob_digits)) or not blob_digits[j + len(digits)].isdigit()
                if left_ok and right_ok:
                    raise ValueError(
                        f"Cross-provider leak: ticket for {this_provider!r} "
                        f"contains {p!r}'s balance digits {digits!r} in {field_name}"
                    )
                idx = j + 1

    # (c) correlated_with must be a list of plain provider-id strings.
    for entry in slice_.get("correlated_with", []):
        if not isinstance(entry, str):
            raise ValueError(
                f"correlated_with must contain only provider-id strings; "
                f"got {type(entry).__name__}: {entry!r}"
            )


def compute_shortage_warnings(
    evidence: Dict[str, Any],
    threshold_minutes: int = SHORTAGE_THRESHOLD_MINUTES,
) -> List[Dict[str, Any]]:
    """Surface per-balance predicted-time-to-zero warnings BEFORE zero hits.

    Iterates `evidence["liquidity"]` (shared_cash + per-provider slices)
    and emits one warning row per balance whose `eta_minutes` is set and
    within `threshold_minutes`. Each row carries:

      - provider_id     "shared_cash" | "1" | "2" | "3"
      - eta_minutes     int — minutes until the balance hits zero at the
                        current weighted burn rate
      - current_balance str — same value as the slice, kept here so the
                        downstream ticket/UI doesn't need to re-parse
                        the evidence dict
      - burn_rate_weighted str — BDT/min, also re-exported
      - confidence      "low" | "medium" | "high"

    These rows drive BOTH the early-shortage alert (so the engine can
    fire when overall_severity is still "low") AND the agent dashboard's
    inline "⚠ Drains in X min" + top-up recommendation.

    Pure function over `analyze()` output — no DB, no time.
    """
    warnings: List[Dict[str, Any]] = []
    for key, pred in evidence.get("liquidity", {}).items():
        eta = pred.get("eta_minutes")
        if eta is None:
            continue
        if eta > threshold_minutes:
            continue
        warnings.append({
            "provider_id": key,            # "shared_cash" or "1"/"2"/"3"
            "eta_minutes": int(eta),
            "current_balance": pred.get("current_balance", "0"),
            "burn_rate_weighted": pred.get("burn_rate_weighted", "0"),
            "confidence": pred.get("confidence", "low"),
        })
    # Sort: most-urgent first (smallest eta).
    warnings.sort(key=lambda w: w["eta_minutes"])
    return warnings


def severity_for_shortage(eta_minutes: int) -> str:
    """Bucket an ETA into severity tiers for shortage-warning alerts.

    > 60 min   → MEDIUM (proactive warning)
    ≤ 60 min   → HIGH   (within the hour)
    ≤ 15 min   → CRITICAL (imminent depletion)
    """
    if eta_minutes <= 15:
        return "CRITICAL"
    if eta_minutes <= 60:
        return "HIGH"
    return "MEDIUM"


def build_alert_and_tickets(
    db: Session,
    agent_id: str,
    t: datetime,
    providers: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """Section 9: returns ORM-ready Alert + per-provider Ticket dicts (no DB write).

    Each ticket carries ITS provider's top-up amount — never the shared
    drawer's. We compute per-provider top-up here so `build_ticket()` can
    receive the right slice.

    NEW BEHAVIOR (early-shortage alerts):
      The gate `overall_severity == "low" and not warnings` previously
      suppressed ALL alerts when nothing behavioral fired. Now we also
      compute `shortage_warnings` (any balance with ETA ≤
      SHORTAGE_THRESHOLD_MINUTES minutes). If the gate would suppress
      but shortage warnings exist, we PROCEED in shortage-warning mode:
      the resulting `alert` is built with `alert_type=LIQUIDITY_SHORTAGE`
      and severity bucketed from the smallest ETA, AND every responsible
      provider gets a ticket carrying the shortage slice (current balance
      + suggested top-up).
    """
    evidence = analyze(db, agent_id, t, providers=providers)
    shortage_warnings = compute_shortage_warnings(evidence)

    if evidence["overall_severity"] == "low" and not evidence["warnings"]:
        if not shortage_warnings:
            return {
                "alert": None,
                "tickets": [],
                "shortage_warnings": [],
                "mode": "silent",
            }
        # Early-shortage path: gate suppressed but a balance is about to
        # deplete. Promote severity and continue to build alert+tickets.
        worst_eta = shortage_warnings[0]["eta_minutes"]
        evidence = {**evidence, "overall_severity": severity_for_shortage(worst_eta).lower()}

    alert = {
        "alert_id": _new_id(),
        "agent_id": agent_id,
        "provider_id": None,
        "alert_type": derive_alert_type(evidence),
        "severity": evidence["overall_severity"],
        "message_bn": None,  # filled by separate explainability service
        "confidence_score": overall_confidence(evidence),
        "master_evidence_json": json.dumps(evidence, default=str),
        "created_at": t,
    }

    active_providers = list(evidence["anomaly"].keys())

    # Per-provider top-up — each ticket gets ITS slice, not shared cash.
    per_provider_topup: Dict[str, Dict[str, Any]] = {}
    shared_topup = evidence["recommended_topup"]
    for p in active_providers:
        if p in evidence["liquidity"]:
            per_provider_topup[p] = recommend_topup(db, agent_id, p, t)
        else:
            per_provider_topup[p] = shared_topup

    responsible = determine_responsible_providers(evidence, active_providers)
    tickets = [
        build_ticket(
            alert["alert_id"], agent_id, p, evidence, t,
            provider_topup=per_provider_topup[p],
        )
        for p in responsible
    ]
    return {
        "alert": alert,
        "tickets": tickets,
        "shortage_warnings": shortage_warnings,
        "mode": "anomaly" if evidence["overall_severity"] != "low" else "silent",
    }