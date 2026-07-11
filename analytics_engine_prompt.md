# Coding Agent Prompt — `analytics_engine.py`

Paste everything below this line into your coding agent (Claude Code, Cursor, GPT, etc.) as the task instruction.

---

## Role

You are implementing the core analytics module for a multi-provider mobile-financial-service (MFS) agent liquidity and risk platform. This module must be **deterministic, reproducible, and fully explainable** — its output feeds `Alert` and `Ticket` rows that drive real operational decisions. It must NOT use an LLM, ML black box, or any non-deterministic component. Every output number must trace back to the exact formula that produced it. Do not substitute your own thresholds, smoothing methods, or heuristics anywhere a formula is given below. Every constant marked `# config` must be a named, overridable parameter, never a hardcoded magic number.

**Money handling rule: use `decimal.Decimal` for every monetary value (amounts, balances, burn rates, top-up amounts) end to end.** Only cast to `float` for statistical intermediates that are inherently ratios/z-scores (never for currency amounts), and only at the point of computation — never store or return currency as float. This matters because transaction amounts arrive as strings (e.g. `"16800.00"`) specifically to avoid float precision loss; casting to float anywhere in the money path reintroduces it.

**Timestamp assumption:** timestamps in the source data are naive (no timezone), e.g. `"2026-07-10T10:30:00"`. Assume these are local time in `Asia/Dhaka` (UTC+6). Document this assumption at the top of the file as a named constant `SOURCE_TZ = "Asia/Dhaka"` and localize consistently — do not silently treat naive timestamps as UTC.

---

## 1. Input data model (matches actual source schema — do not rename fields)

```python
from decimal import Decimal
from datetime import datetime
from enum import Enum

class TxType(str, Enum):
    CASH_IN = "CASH_IN"
    CASH_OUT = "CASH_OUT"

@dataclass
class TransactionEvent:
    tx_id: str
    agent_id: str
    provider_id: str            # "bkash" | "nagad" | "rocket"
    customer_id_hash: str       # synthetic/anonymized — never resolve to real identity
    tx_type: TxType
    amount: Decimal              # parse from string, e.g. Decimal("16800.00")
    timestamp: datetime          # naive, Asia/Dhaka local time — see assumption above

@dataclass
class AgentRecord:
    agent_id: str
    shop_name: str
    area: str
    district: str
    shared_physical_cash: Decimal
    status: str                  # "ACTIVE" | ...
```

**Gap to flag, not silently invent:** the provided `Agent` schema only carries `shared_physical_cash` (the pooled cash drawer). It does not carry per-provider e-money balances. Assume a parallel table/service exists — `get_provider_balance(agent_id, provider_id) -> Decimal` — and call it as an external dependency; do not fabricate its schema here. If it doesn't exist yet in the codebase, raise a clear `NotImplementedError` with a message naming this dependency, rather than guessing a shape for it.

Assume these data-access functions already exist (implement calls against them, not their internals):

```python
def get_transactions(agent_id: str, provider_id: str | None, start: datetime, end: datetime) -> list[TransactionEvent]: ...
def get_shared_cash_balance(agent_id: str) -> Decimal: ...
def get_provider_balance(agent_id: str, provider_id: str) -> Decimal: ...
def get_agent(agent_id: str) -> AgentRecord: ...
```

`provider_id=None` in any function below always means "shared physical cash," matching how `Alert.provider_id` is nullable in the ORM (a combined/shared-cash alert has no single provider).

---

## 2. Rolling-window burn rate

For `(agent_id, provider_id_or_none)` at evaluation time `t`, window length `w` minutes:

```
cash_out(w) = sum(tx.amount for tx in window if tx.tx_type == CASH_OUT)
cash_in(w)  = sum(tx.amount for tx in window if tx.tx_type == CASH_IN)
burn_rate(w) = (cash_out(w) - cash_in(w)) / Decimal(w)        # BDT per minute, Decimal throughout
```

Compute for **w ∈ {5, 10, 30} minutes**.  a Start Simulation button with window size: 5/10/30 mins

```
burn_rate_weighted = Decimal("0.5") * burn_rate(5) + Decimal("0.3") * burn_rate(10) + Decimal("0.2") * burn_rate(30)
```

`WINDOW_WEIGHTS = {5: 0.5, 10: 0.3, 30: 0.2} # config`

For shared cash (`provider_id=None`), sum `cash_out`/`cash_in` across **all** providers' transactions for that agent — cash-out from any provider draws down the same physical drawer.

---

## 3. Historical baseline (robust statistics — median/MAD)

Same `(agent_id, provider_id_or_none)`, same time-of-day bucket (hourly, in `Asia/Dhaka`) and day-type (`weekday` / `weekend` / `pre_eid` / `salary_day` — config-driven calendar), across the last `N = 14 # config` historical days.

```
baseline_median(w) = median(historical_burn_rate_samples(w))     # Decimal
baseline_MAD(w)     = median(|x - baseline_median(w)| for x in historical_burn_rate_samples(w))
baseline_sigma(w)   = Decimal("1.4826") * baseline_MAD(w)
```

**Cold-start fallback:** if fewer than `MIN_BASELINE_SAMPLES = 5 # config` samples exist for this exact agent, pool across all agents in the same `area` (from `AgentRecord.area`), same time bucket, same day-type. Tag `baseline_source = "agent" | "pooled" | "insufficient"`. If pooled data is also insufficient, return `baseline_source = "insufficient"` and force downstream `confidence = "low"`.

Guard every division with `epsilon = Decimal("0.000001")`.

---

## 4. Liquidity shortage prediction (ETA)

```
spike_ratio(w) = burn_rate(w) / max(baseline_median(w), epsilon)
change_pct(w)  = (burn_rate(w) - baseline_median(w)) / max(baseline_median(w), epsilon) * 100
```

```
if burn_rate_weighted <= 0:
    eta_minutes = None
else:
    current_balance = get_shared_cash_balance(agent_id) if provider_id is None else get_provider_balance(agent_id, provider_id)
    eta_minutes = (current_balance - safety_floor) / burn_rate_weighted
```

`safety_floor # config`, default `Decimal("0")`, overridable per agent/provider.

**Confidence band** (uncertainty from spread across the three windows):

```
sigma_burn = stdev([burn_rate(5), burn_rate(10), burn_rate(30)])   # float ok here, ratio-like intermediate

eta_low  = (current_balance - safety_floor) / (burn_rate_weighted + sigma_burn)
eta_high = (current_balance - safety_floor) / max(burn_rate_weighted - sigma_burn, epsilon)
```

```
relative_uncertainty = sigma_burn / max(burn_rate_weighted, epsilon)

confidence =
    "high"   if relative_uncertainty < 0.15 and baseline_source == "agent"
    "medium" if relative_uncertainty < 0.40
    "low"    otherwise

confidence = "low" if baseline_source == "insufficient" else confidence   # overrides
```

---

## 5. Safe backup / top-up amount

```
required_for_window = burn_rate_weighted * Decimal(target_coverage_minutes)   # config, default 60
raw_topup = max(Decimal("0"), required_for_window - current_balance)

safety_margin = base_margin + margin_sensitivity * Decimal(str(relative_uncertainty))
    # base_margin = Decimal("0.10") # config
    # margin_sensitivity = Decimal("0.5") # config

topup_amount = (raw_topup * (1 + safety_margin)).quantize(Decimal("100"), rounding=ROUND_HALF_UP)
```

Return `Decimal("0")` if `raw_topup == 0`.

---

## 6. Anomaly detection

### 6a. Velocity anomaly (per agent, per provider)

```
count_z(w)  = (observed_count(w) - baseline_median_count(w)) / max(baseline_sigma_count(w), epsilon)
amount_z(w) = (burn_rate(w) - baseline_median(w)) / max(baseline_sigma(w), epsilon)
velocity_score(w) = 0.5 * float(count_z(w)) + 0.5 * float(amount_z(w))   # score is a ratio, float is fine here
```

Flag `velocity_anomaly = True` if `velocity_score(w) > Z_THRESHOLD` (`Z_THRESHOLD = 3.0 # config`) for any `w`. Report the triggering window and max score.

### 6b. Structuring / near-duplicate pattern (uses `customer_id_hash`, not a generic account ref)

Window `w = 15 # config` minutes, only `CASH_OUT` transactions for one `(agent_id, provider_id)`.

```
def same_cluster(a: Decimal, b: Decimal, tolerance=Decimal("0.05")) -> bool:
    return abs(a - b) / max(a, b, epsilon) <= tolerance
```

Sort by amount, greedily group adjacent values satisfying `same_cluster`. For the largest cluster:

```
n_cluster              = count of transactions in cluster
distinct_customers     = set of customer_id_hash in cluster        # cluster's "who"
k_distinct              = len(distinct_customers)
n_total                 = total CASH_OUT transactions in window
structuring_ratio       = n_cluster / max(n_total, 1)
account_concentration   = n_cluster / max(k_distinct, 1)
```

Flag `structuring_anomaly = True` if:
```
n_cluster >= MIN_CLUSTER_SIZE            # config, default 4
k_distinct <= MAX_DISTINCT_CUSTOMERS     # config, default 3
structuring_ratio >= STRUCTURING_RATIO_THRESHOLD   # config, default 0.30
```

`flagged_customers = list(distinct_customers)` — this is the exact list that goes into the provider-scoped ticket evidence (see section 9). Never merge `customer_id_hash` values across different `provider_id`s into one identity — a hash from bKash and a hash from Nagad are different namespaces and must never be treated as the same customer.

### 6c. Cross-provider correlation

For an agent's providers `P` (distinct `provider_id`s seen in its transactions), compute `velocity_score(w=10)` independently per provider:

```
correlated_providers = [p for p in P if velocity_score_by_provider[p] > Z_THRESHOLD]
correlated = len(correlated_providers) >= 2
severity_multiplier = 1.5 if correlated else 1.0
```

### 6d. Balance reconciliation (data-quality, not a fraud signal)

```
expected_balance = opening_balance + sum(CASH_IN over period) - sum(CASH_OUT over period)
reconciliation_error = abs(reported_balance - expected_balance)
reconciliation_error_pct = reconciliation_error / max(reported_balance, epsilon)
data_quality_flag = reconciliation_error_pct > RECON_TOLERANCE   # config, default Decimal("0.01")
```

If `True`: do not raise severity from this alone. Force `confidence = "low"` for that agent/provider and add `"data_quality_issue"` to `warnings`.

### 6e. Composite anomaly score and severity

```
def normalize(z: float, cap: float = 6.0) -> float:
    return min(max(z, 0), cap) / cap

composite_score = (
    0.4 * normalize(velocity_score) +
    0.4 * (float(structuring_ratio) if structuring_anomaly else 0.0) +
    0.2 * (1.0 if correlated else 0.0)
)
composite_score = min(composite_score * severity_multiplier, 1.0)

severity =
    "high"   if composite_score >= 0.66
    "medium" if composite_score >= 0.33
    "low"    otherwise
```

**Hard cap:** if `correlated == False` and `structuring_anomaly == False`, cap `severity` at `"medium"` even if `composite_score` would round to `"high"` — a single-provider velocity spike alone is never enough for the top severity tier.

---

## 7. Required public functions

```python
def compute_burn_rate(agent_id: str, provider_id: str | None, t: datetime) -> dict: ...
def compute_baseline(agent_id: str, provider_id: str | None, t: datetime, window: int) -> dict: ...
def predict_shortage(agent_id: str, provider_id: str | None, t: datetime) -> dict: ...
def recommend_topup(agent_id: str, provider_id: str | None, t: datetime) -> dict: ...
def detect_velocity_anomaly(agent_id: str, provider_id: str, t: datetime) -> dict: ...
def detect_structuring(agent_id: str, provider_id: str, t: datetime) -> dict: ...
def detect_cross_provider_correlation(agent_id: str, t: datetime) -> dict: ...
def check_balance_reconciliation(agent_id: str, provider_id: str | None, t: datetime) -> dict: ...

def analyze(agent_id: str, t: datetime) -> dict:
    """Top-level entry point: combines all of the above into ONE master evidence
    object for the agent (all providers + shared cash). This is the only function
    the alert/ticket generation layer should call."""

def build_alert_and_tickets(agent_id: str, t: datetime) -> dict:
    """See section 9. Returns ORM-ready dicts for one Alert row and one or more
    Ticket rows, matching the SQLModel classes in the codebase exactly."""
```

---

## 8. Master evidence object (from `analyze()` — becomes `Alert.master_evidence_json`)

```json
{
  "agent_id": "agent1000",
  "evaluated_at": "2026-07-10T10:45:00",
  "liquidity": {
    "shared_cash": {"eta_minutes": 42, "eta_range_minutes": [35, 51], "confidence": "high",
                     "current_balance": "84000.00", "burn_rate_weighted": "2000.00", "change_pct": 300.0},
    "bkash":  {"eta_minutes": null, "burn_rate_weighted": "1200.00", "change_pct": 300.0},
    "nagad":  {"eta_minutes": null, "burn_rate_weighted": "800.00",  "change_pct": 300.0}
  },
  "recommended_topup": {"amount": "40000.00", "target_coverage_minutes": 60},
  "anomaly": {
    "bkash": {"velocity_anomaly": true, "structuring_anomaly": false, "flagged_customers": [], "composite_score": 0.58},
    "nagad": {"velocity_anomaly": true, "structuring_anomaly": true, "flagged_customers": ["cust_hash_023", "cust_hash_091"], "composite_score": 0.74}
  },
  "correlated_providers": ["bkash", "nagad"],
  "overall_severity": "high",
  "warnings": [],
  "data_quality_flag": false
}
```

All monetary fields are strings (serialized `Decimal`) to preserve precision through JSON — never serialize as JSON numbers, which silently become floats.

---

## 9. Alert & Ticket generation logic (maps `analyze()` output onto your actual ORM models)

```python
def build_alert_and_tickets(agent_id: str, t: datetime) -> dict:
    evidence = analyze(agent_id, t)

    if evidence["overall_severity"] == "low" and not evidence["warnings"]:
        return {"alert": None, "tickets": []}   # nothing rises to alert-worthy

    alert = {
        "alert_id": new_id(),
        "agent_id": agent_id,
        "provider_id": None,               # combined/shared alert; nullable per Alert model
        "alert_type": derive_alert_type(evidence),   # e.g. LIQUIDITY_SHORTAGE / ANOMALY / COMBINED
        "severity": evidence["overall_severity"],
        "message_bn": None,                # filled by the separate explainability/LLM service, NOT here
        "confidence_score": overall_confidence(evidence),   # Decimal, min confidence across contributing components
        "master_evidence_json": json.dumps(evidence, default=str),
        "created_at": t,
    }

    responsible_providers = determine_responsible_providers(evidence)
    tickets = [build_ticket(alert["alert_id"], agent_id, provider_id, evidence, t)
               for provider_id in responsible_providers]

    return {"alert": alert, "tickets": tickets}
```

**`determine_responsible_providers(evidence)`** — a provider is "responsible" (gets its own ticket) if ANY of:
- `evidence["anomaly"][provider_id]["velocity_anomaly"]` or `["structuring_anomaly"]` is `True`, OR
- `provider_id in evidence["correlated_providers"]`, OR
- for a **shared-cash-only** shortage with no per-provider anomaly flags at all: compute each provider's contribution share to the shared burn rate —
  ```
  contribution_share(p) = burn_rate_weighted(p) / max(sum(burn_rate_weighted(p) for p in P), epsilon)
  ```
  include any provider where `contribution_share(p) >= 0.20 # config` (20%), so a liquidity-only alert still routes to the provider(s) actually driving the drain, not to all providers indiscriminately.

**One `Ticket` per responsible provider** — this is what keeps provider boundaries intact even inside the coordination layer: a Nagad territory officer's ticket must only ever contain Nagad's slice of the evidence.

```python
def build_ticket(alert_id: str, agent_id: str, provider_id: str, evidence: dict, t: datetime) -> dict:
    provider_slice = {
        "liquidity": evidence["liquidity"].get(provider_id) or evidence["liquidity"]["shared_cash"],
        "anomaly": evidence["anomaly"].get(provider_id, {}),
        "flagged_customers": evidence["anomaly"].get(provider_id, {}).get("flagged_customers", []),
        "correlated_with": [p for p in evidence["correlated_providers"] if p != provider_id],
        "recommended_topup": evidence["recommended_topup"],
        "data_quality_flag": evidence["data_quality_flag"],
    }
    return {
        "ticket_id": new_id(),
        "alert_id": alert_id,
        "provider_id": provider_id,
        "assigned_officer_id": route_to_officer(agent_id, provider_id),   # existing routing service, not implemented here
        "current_owner_role": "TERRITORY_OFFICER",   # initial owner, per OwnerRole enum
        "status": "OPEN",                              # per TicketStatus enum
        "evidence_json": json.dumps(provider_slice, default=str),
        "created_at": t,
        "updated_at": t,
    }
```

`correlated_with` is deliberately just the *list of other provider IDs*, never their raw numbers — a territory officer learns "this correlates with Nagad" without seeing Nagad's balances/customers, preserving the boundary rule from the challenge brief even in a correlated-spike case.

**Initial `AuditLog` row** — the caller (ticket orchestration service, not this file) should immediately write one `AuditLog` per created ticket: `action_taken = "CREATED"`, `performed_by_role = "SYSTEM"`, `notes_text = f"Auto-generated from {alert['alert_type']} evidence, severity={alert['severity']}"`. This file only needs to return data shaped so that step is trivial — do not write to the database directly from `analytics_engine.py`.

---

## 10. Guardrails — do not violate these

- No LLM calls, no ML model, no non-deterministic function anywhere in this file. Identical input data must produce byte-identical output.
- All money as `Decimal`, never `float`, from parsing through to `topup_amount` and JSON serialization (as strings).
- Never divide without an `epsilon` guard.
- `data_quality_flag=True` never raises severity — only lowers confidence.
- Single-provider spike with no correlation and no structuring is hard-capped at `"medium"` severity (section 6e).
- Never merge `customer_id_hash` across providers into one identity, in code or in ticket evidence.
- `message_bn` (the Bangla explanation) is explicitly out of scope for this file — it's produced by a separate LLM-based explainability service that consumes `master_evidence_json`, never generated here.
- One `Ticket` per responsible provider, never one ticket bundling multiple providers' evidence.
- Every threshold/weight/window is a named `Config` constant, never an inline literal.
- All public functions have docstrings quoting the formula they implement.

---

## 11. Tests to include in `test_analytics_engine.py`

1. Worked example: `shared_physical_cash=Decimal("84000.00")`, `burn_rate_weighted=Decimal("2000.00")` → `eta_minutes == 42`.
2. Same inputs, `target_coverage_minutes=60` → `topup_amount == Decimal("40000.00")`.
3. Cold-start: agent with < 5 historical samples → `baseline_source in ("pooled", "insufficient")`, no exception.
4. Zero/negative burn rate → `eta_minutes is None`.
5. Single-provider spike, no correlation, no structuring → `severity != "high"` (hard cap enforced).
6. Structuring: 5 `CASH_OUT` transactions within 3% of each other from 2 distinct `customer_id_hash` values → `structuring_anomaly == True` and `flagged_customers` contains exactly those 2 hashes.
7. Reconciliation mismatch → `data_quality_flag == True`, confirms severity is NOT elevated by it alone.
8. `build_alert_and_tickets`: correlated bKash + Nagad spike → exactly 2 tickets returned, each `evidence_json` containing only that provider's `flagged_customers` and liquidity numbers (assert the other provider's raw numbers are absent from each ticket's evidence).
9. Shared-cash-only shortage with `bkash` contributing 75% of burn and `nagad` contributing 25% → only `bkash` ticket created (nagad below the 20% contribution threshold — adjust the test fixture to cross/not-cross that line explicitly).
10. Decimal precision: sum of many small `Decimal` amounts in a window must not drift versus a hand-computed expected sum (regression guard against any accidental float cast).
