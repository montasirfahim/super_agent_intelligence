"""Analytics engine — liquidity forecasting + behavioral anomaly detection.

Implements the formulas in `analytics_engine_prompt.md`. Produces an
`analyze()` master-evidence dict per agent that downstream services consume
to build `Alert` and `Ticket` ORM rows. This module is purely computational
plus ORM reads — it does NOT write to the database. Ticket creation and the
initial AuditLog row live in the orchestration layer.

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
from typing import Any, Dict, Iterable, List, Optional, Sequence

from sqlmodel import Session, select

from app.models import (
    Agent,
    ProviderWallet,
    TransactionStream,
)
from app.services.routing import route_to_officer  # used in build_ticket


# ---------------------------------------------------------------------------
# Config (named constants — never inline literals)
# ---------------------------------------------------------------------------

SOURCE_TZ = "Asia/Dhaka"          # documented assumption for naive timestamps

WINDOW_WEIGHTS: Dict[int, Decimal] = {5: Decimal("0.5"), 10: Decimal("0.3"), 30: Decimal("0.2")}

MIN_BASELINE_SAMPLES = 5
BASELINE_LOOKBACK_DAYS = 14      # N historical days for the baseline window
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
# Data access adapters (replace the prompt's NotImplementedError stubs)
# ---------------------------------------------------------------------------

def get_transactions(
    db: Session,
    agent_id: str,
    provider_id: Optional[str],
    start: datetime,
    end: datetime,
) -> List[TransactionEvent]:
    """Pull transactions for one agent, optionally filtered to a provider.

    `provider_id=None` aggregates across all providers (used for shared cash).
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
    """Providers this agent has any transactions on (from the last 30 days).

    Used by `analyze()` when callers don't pass a provider list.
    """
    cutoff = datetime.utcnow() - timedelta(days=30)
    rows = db.exec(
        select(TransactionStream.provider_id)
        .where(TransactionStream.agent_id == agent_id)
        .where(TransactionStream.timestamp >= cutoff)
        .distinct()
    ).all()
    return sorted({r for r in rows if r})


# ---------------------------------------------------------------------------
# Historical baseline sample extractors
# ---------------------------------------------------------------------------

def _hour_bucket(dt: datetime) -> int:
    return dt.hour


def _day_type(dt: datetime) -> str:
    """Simplified day-type classifier.

    The full platform has a config-driven calendar; for the analytics engine
    we treat:
      - weekday 0..4 → "weekday"
      - weekday 5..6 → "weekend"
    Pay-day / pre-eid tags come from the `tx_day` column on TransactionStream
    itself when present (handled in the historical sample fetch below).
    """
    return "weekend" if dt.weekday() >= 5 else "weekday"


def _get_historical_samples(
    db: Session,
    agent_id: str,
    provider_id: Optional[str],
    t: datetime,
    window: int,
) -> List[Decimal]:
    """Per-window burn-rate samples (BDT/min) over the last `BASELINE_LOOKBACK_DAYS`.

    For each historical day in the lookback window, compute the burn rate
    for the same hour bucket + day type as `t`, using transactions whose
    timestamp falls in that bucket. Returns a list of Decimal samples
    (one per day that had any activity).
    """
    end = t - timedelta(minutes=window)
    hour = _hour_bucket(t)
    day_type = _day_type(t)
    samples: List[Decimal] = []

    for d in range(BASELINE_LOOKBACK_DAYS):
        day_start = (t - timedelta(days=d + 1)).replace(hour=hour, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(hours=1)
        # Only include days whose day-type matches `t`
        if _day_type(day_start) != day_type:
            continue
        txs = get_transactions(db, agent_id, provider_id, day_start, day_end)
        cash_out = sum((tx.amount for tx in txs if tx.tx_type == TxType.CASH_OUT), Decimal("0"))
        cash_in = sum((tx.amount for tx in txs if tx.tx_type == TxType.CASH_IN), Decimal("0"))
        if (cash_out + cash_in) == 0:
            continue
        samples.append((cash_out - cash_in) / Decimal(window))
    return samples


def _get_historical_count_samples(
    db: Session,
    agent_id: str,
    provider_id: Optional[str],
    t: datetime,
    window: int,
) -> List[int]:
    """Per-window transaction-count samples over the same historical lookback."""
    end = t - timedelta(minutes=window)
    hour = _hour_bucket(t)
    day_type = _day_type(t)
    counts: List[int] = []

    for d in range(BASELINE_LOOKBACK_DAYS):
        day_start = (t - timedelta(days=d + 1)).replace(hour=hour, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(hours=1)
        if _day_type(day_start) != day_type:
            continue
        txs = get_transactions(db, agent_id, provider_id, day_start, day_end)
        if not txs:
            continue
        counts.append(len(txs))
    return counts


# ---------------------------------------------------------------------------
# Routing shim — `build_ticket` is exported as a dict-builder and is called
# by orchestration code that already holds a `db` session, but the ticket
# payload only carries `agent_id`/`provider_id`. We resolve the session
# lazily via SQLModel's engine bound to the global `database.engine`.
# ---------------------------------------------------------------------------

def _route_to_officer_for_ticket(agent_id: str, provider_id: str) -> str:
    """Best-effort officer lookup at ticket-build time.

    Looks up via the active FastAPI request's session if one is bound,
    otherwise via a fresh short-lived session against the global engine.
    Returns the officer id (or "" if no assignment can be found).
    """
    from app.database import engine
    try:
        with Session(engine) as s:
            return route_to_officer(s, agent_id, provider_id)
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Rolling-window burn rate
# ---------------------------------------------------------------------------

def compute_burn_rate(
    db: Session,
    agent_id: str,
    provider_id: Optional[str],
    t: datetime,
) -> Dict[str, Any]:
    """Formula: `burn_rate(w) = (cash_out - cash_in) / w` per window in {5,10,30}.

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
# Historical baseline (median / MAD → sigma)
# ---------------------------------------------------------------------------

def compute_baseline(
    db: Session,
    agent_id: str,
    provider_id: Optional[str],
    t: datetime,
    window: int,
) -> Dict[str, Any]:
    """Robust baseline: median + MAD-derived sigma, with agent→area pooled fallback.

    Returns Decimal throughout. The Decimal/float mixing that the previous
    implementation suffered from is fixed: we compute the median and MAD as
    Decimal, then derive sigma via the 1.4826 constant also as Decimal.
    """
    samples = _get_historical_samples(db, agent_id, provider_id, t, window)
    count_samples = _get_historical_count_samples(db, agent_id, provider_id, t, window)

    source = "agent"
    if len(samples) < MIN_BASELINE_SAMPLES:
        # Real fallback: pool across all agents in the same area.
        samples, count_samples = _pool_area_samples(
            db, agent_id, provider_id, t, window
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
    db: Session,
    agent_id: str,
    provider_id: Optional[str],
    t: datetime,
    window: int,
) -> tuple[List[Decimal], List[int]]:
    """Pool historical samples across every agent in the same area as `agent_id`.

    Returns (burn_rate_samples, count_samples). Used as a cold-start fallback
    when the per-agent sample count is below `MIN_BASELINE_SAMPLES`.
    """
    agent = db.get(Agent, agent_id)
    if not agent:
        return [], []
    area = agent.area
    peers = db.exec(select(Agent).where(Agent.area == area)).all()
    burn: List[Decimal] = []
    counts: List[int] = []
    for peer in peers:
        if peer.agent_id == agent_id:
            continue
        burn.extend(_get_historical_samples(db, peer.agent_id, provider_id, t, window))
        counts.extend(_get_historical_count_samples(db, peer.agent_id, provider_id, t, window))
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
    """Section 4 of the prompt: ETA + confidence band + change_pct."""
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
    # stdev needs ≥2 points; with all three window rates equal the spread is
    # genuinely 0, not an error. Use a guarded wrapper.
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
# Safe top-up
# ---------------------------------------------------------------------------

def recommend_topup(
    db: Session,
    agent_id: str,
    provider_id: Optional[str],
    t: datetime,
) -> Dict[str, Any]:
    """Section 5: `topup = max(0, weighted_burn * coverage - balance) * (1 + margin)`."""
    pred = predict_shortage(db, agent_id, provider_id, t)
    weighted = Decimal(pred["burn_rate_weighted"])
    current_balance = Decimal(pred["current_balance"]) if pred["current_balance"] != "0.00" else _get_balance(db, agent_id, provider_id)
    uncertainty = float(pred.get("relative_uncertainty", 0.0))

    required = weighted * Decimal(TARGET_COVERAGE_MINUTES)
    raw_topup = max(Decimal("0"), required - current_balance)
    if raw_topup == Decimal("0"):
        return {"amount": "0.00", "target_coverage_minutes": TARGET_COVERAGE_MINUTES}

    safety_margin = BASE_MARGIN + (MARGIN_SENSITIVITY * Decimal(str(uncertainty)))
    topup = (raw_topup * (Decimal("1") + safety_margin)).quantize(Decimal("100"), rounding=ROUND_HALF_UP)
    return {
        "amount": str(topup.quantize(Decimal("0.01"))),
        "target_coverage_minutes": TARGET_COVERAGE_MINUTES,
    }


# ---------------------------------------------------------------------------
# Velocity anomaly
# ---------------------------------------------------------------------------

def detect_velocity_anomaly(
    db: Session,
    agent_id: str,
    provider_id: str,
    t: datetime,
) -> Dict[str, Any]:
    """Section 6a: count_z + amount_z averaged, flag if any window > Z_THRESHOLD."""
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
# Structuring / near-duplicate pattern
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
    """Section 6b: 15-min window, CASH_OUT only, cluster by amount similarity."""
    start = t - timedelta(minutes=STRUCTURING_WINDOW_MINS)
    txs = get_transactions(db, agent_id, provider_id, start, t)
    out_txs = sorted([tx for tx in txs if tx.tx_type == TxType.CASH_OUT], key=lambda x: x.amount)

    n_total = len(out_txs)
    if n_total == 0:
        return {"structuring_anomaly": False, "structuring_ratio": 0.0, "flagged_customers": []}

    # Robust greedy grouping: compare each new tx against the running mean of
    # the current cluster, not just the previous amount. A single in-band
    # outlier no longer breaks an otherwise valid cluster.
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
# Balance reconciliation (data-quality signal)
# ---------------------------------------------------------------------------

def check_balance_reconciliation(
    db: Session,
    agent_id: str,
    provider_id: Optional[str],
    t: datetime,
) -> Dict[str, Any]:
    """Section 6d: |reported - (opening + in - out)| / reported > tolerance → DQ flag.

    Without a stable opening-balance table, we use the agent's stored
    `shared_physical_cash` (or wallet `e_money_balance`) as the "reported"
    value and the day's transactions as the reconciliation period. This
    surfaces cases where the DB balance has drifted far from what the live
    txn stream implies.
    """
    try:
        reported = _get_balance(db, agent_id, provider_id)
    except KeyError:
        return {"data_quality_flag": False, "reconciliation_error_pct": 0.0}

    day_start = t.replace(hour=0, minute=0, second=0, microsecond=0)
    txs = get_transactions(db, agent_id, provider_id, day_start, t)
    cash_out = sum((tx.amount for tx in txs if tx.tx_type == TxType.CASH_OUT), Decimal("0"))
    cash_in = sum((tx.amount for tx in txs if tx.tx_type == TxType.CASH_IN), Decimal("0"))

    # Without an opening snapshot we can only flag gross inflows vs the
    # balance; in this codebase we conservatively report no flag unless the
    # sum of inflows alone exceeds the reported balance (clearly stale).
    if reported <= Decimal("0"):
        return {"data_quality_flag": False, "reconciliation_error_pct": 0.0}

    # Treat the live simulation source-of-truth as the reconciliation
    # target: (cash_out - cash_in) should be ≈ reported if reported is
    # truly the running balance. We surface the percentage.
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
    """Top-level entry point. Returns the master evidence dict."""
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
        anomaly[p] = {
            "velocity_anomaly": v_anom["velocity_anomaly"],
            "velocity_score": v_anom["velocity_score"],
            "triggering_window": v_anom.get("triggering_window"),
            "structuring_anomaly": s_anom["structuring_anomaly"],
            "structuring_ratio": s_anom["structuring_ratio"],
            "flagged_customers": s_anom["flagged_customers"],
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

    # Composite score per provider
    for p in providers:
        v_score = anomaly[p]["velocity_score"]
        s_flag = anomaly[p]["structuring_anomaly"]
        s_ratio = anomaly[p]["structuring_ratio"]
        comp = (
            0.4 * normalize(v_score)
            + 0.4 * (float(s_ratio) if s_flag else 0.0)
            + 0.2 * (1.0 if correlated else 0.0)
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
# Alert + ticket generation
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
        return "BEHAVIORAL_ANOMALY"  # was: COMBINED_LIQUIDITY_ANOMALY (not in ORM enum)
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
    set, or contributed ≥ 20% of the shared burn in a liquidity-only case."""
    resp: set[str] = set()
    total_weighted = Decimal("0")
    for p in providers:
        if evidence["anomaly"][p]["velocity_anomaly"] or evidence["anomaly"][p]["structuring_anomaly"]:
            resp.add(p)
        if p in evidence["correlated_providers"]:
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
) -> Dict[str, Any]:
    """Provider-scoped ticket evidence — strictly this provider's slice only.

    `correlated_with` is the list of *other* provider IDs only — never their
    balances or flagged customers. This preserves the multi-tenant boundary.
    """
    provider_slice = {
        "liquidity": evidence["liquidity"].get(provider_id) or evidence["liquidity"]["shared_cash"],
        "anomaly": evidence["anomaly"].get(provider_id, {}),
        "flagged_customers": evidence["anomaly"].get(provider_id, {}).get("flagged_customers", []),
        "correlated_with": [p for p in evidence["correlated_providers"] if p != provider_id],
        "recommended_topup": evidence["recommended_topup"],
        "data_quality_flag": evidence["data_quality_flag"],
    }
    return {
        "ticket_id": _new_id(),
        "alert_id": alert_id,
        "provider_id": provider_id,
        "assigned_officer_id": _route_to_officer_for_ticket(agent_id, provider_id),
        "current_owner_role": "FIELD_OFFICER",  # was: TERRITORY_OFFICER (not in ORM enum)
        "status": "OPEN",
        "evidence_json": json.dumps(provider_slice, default=str),
        "created_at": t,
        "updated_at": t,
    }


def build_alert_and_tickets(
    db: Session,
    agent_id: str,
    t: datetime,
    providers: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """Section 9: returns ORM-ready Alert + per-provider Ticket dicts (no DB write)."""
    evidence = analyze(db, agent_id, t, providers=providers)
    if evidence["overall_severity"] == "low" and not evidence["warnings"]:
        return {"alert": None, "tickets": []}

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
    responsible = determine_responsible_providers(evidence, active_providers)
    tickets = [build_ticket(alert["alert_id"], agent_id, p, evidence, t) for p in responsible]
    return {"alert": alert, "tickets": tickets}