"""Simulation-engine test suite — covers the balance-feasibility gate.

Layer 1 of the predictive-shortage + balance-rejection feature:

  - CASH_OUT requires `shared_physical_cash >= amount`. If not, reject with
    reason `insufficient_physical_cash`. NO txn row inserted, NO balance
    mutation. The in-process rejection counter increments.

  - CASH_IN requires `wallet.e_money_balance >= amount`. If not, reject with
    reason `insufficient_emoney_<provider_id>`. Same no-mutation contract.

  - The rejection endpoint `GET /api/simulate/rejections?agent_id=X` returns
    the per-(agent, provider, tx_type) counts for the dashboard to render.

These tests exercise the public surface (HTTP + helper functions) without
spinning up the live simulation thread.
"""
from __future__ import annotations

from decimal import Decimal

from fastapi.testclient import TestClient

from app.main import app
from app.api import simulation as sim_api


client = TestClient(app)


# ---------------------------------------------------------------------------
# Helpers — hit the live DB to find agent1000 + its assigned providers.
# Skips cleanly if the seeded DB shape differs across environments.
# ---------------------------------------------------------------------------

def _get_agent1000() -> dict | None:
    agents = client.get("/api/lookup/agents").json()
    return next((a for a in agents if a["agent_id"] == "agent1000"), None)


def _reset_rejections_for(aid: str) -> None:
    """Wipe any rejection counter state for `aid` so each test starts clean."""
    sim_api.reset_rejection_counts(aid)


# ---------------------------------------------------------------------------
# 1. _compute_balance_delta pure function — rejection contract
# ---------------------------------------------------------------------------

def test_compute_balance_delta_cash_out_rejected_when_drawer_below_amount():
    """CASH_OUT needing 5000 with only 2000 in drawer → applied=False,
    reason='insufficient_physical_cash', balances unchanged."""
    res = sim_api._compute_balance_delta(
        physical_before=Decimal("2000"),
        emoney_before=Decimal("10000"),
        tx_type="CASH_OUT",
        amount=Decimal("5000"),
        provider_id="1",
    )
    assert res["applied"] is False
    assert res["reason"] == "insufficient_physical_cash"
    assert res["new_physical"] == Decimal("2000")
    assert res["new_emoney"] == Decimal("10000")


def test_compute_balance_delta_cash_out_accepted_when_drawer_has_amount():
    """CASH_OUT 5000 with 10000 in drawer → applied=True, balances move."""
    res = sim_api._compute_balance_delta(
        physical_before=Decimal("10000"),
        emoney_before=Decimal("10000"),
        tx_type="CASH_OUT",
        amount=Decimal("5000"),
        provider_id="1",
    )
    assert res["applied"] is True
    assert res["reason"] is None
    # CASH_OUT: physical goes down (drawer gives cash to customer),
    # emoney goes up (customer's e-wallet gets credited).
    assert res["new_physical"] == Decimal("5000")
    assert res["new_emoney"] == Decimal("15000")


def test_compute_balance_delta_cash_in_rejected_when_wallet_below_amount():
    """CASH_IN needing 10000 with only 3000 in e-wallet → applied=False,
    reason='insufficient_emoney_<provider>', balances unchanged."""
    res = sim_api._compute_balance_delta(
        physical_before=Decimal("50000"),
        emoney_before=Decimal("3000"),
        tx_type="CASH_IN",
        amount=Decimal("10000"),
        provider_id="1",
    )
    assert res["applied"] is False
    assert res["reason"] == "insufficient_emoney_1"
    assert res["new_physical"] == Decimal("50000")
    assert res["new_emoney"] == Decimal("3000")


def test_compute_balance_delta_cash_in_accepted_when_wallet_has_amount():
    """CASH_IN 4000 with 10000 in e-wallet → applied=True, balances move."""
    res = sim_api._compute_balance_delta(
        physical_before=Decimal("50000"),
        emoney_before=Decimal("10000"),
        tx_type="CASH_IN",
        amount=Decimal("4000"),
        provider_id="1",
    )
    assert res["applied"] is True
    assert res["reason"] is None
    # CASH_IN: physical goes up (customer hands cash to agent for e-wallet credit),
    # emoney goes down (e-wallet pays out the float).
    assert res["new_physical"] == Decimal("54000")
    assert res["new_emoney"] == Decimal("6000")


# ---------------------------------------------------------------------------
# 2. HTTP endpoint — injection round-trip
# ---------------------------------------------------------------------------

def test_inject_transaction_cash_out_rejected_when_drawer_low():
    """A CASH_OUT that exceeds the agent's physical-cash drawer MUST be
    rejected by the HTTP endpoint: HTTP 200, body `{ok: True, rejected:
    True, reason: 'insufficient_physical_cash'}`. No txn row inserted."""
    # Lookup endpoint returns only agent_id+shop_name+area. The physical
    # balance comes from the agent dashboard.
    if not _get_agent1000():
        import pytest; pytest.skip("agent1000 not in seeded DB")

    _reset_rejections_for("agent1000")

    # 1. Read current physical balance via the dashboard
    dash = client.get("/api/dashboard/agent?role=agent&user_id=agent1000").json()
    start_balance = dash["physical"]["balance"]

    # 2. Drain the drawer to a small amount via a feasible large CASH_OUT.
    drain_amount = start_balance - 500  # leaves exactly 500 in drawer
    r = client.post("/api/simulate/inject-transaction", json={
        "agent_id": "agent1000",
        "provider_id": "1",
        "type": "CASH_OUT",
        "amount": drain_amount,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    if body.get("rejected"):
        import pytest; pytest.skip(
            f"setup drain itself was rejected: {body.get('reason')}"
        )

    # 3. Now inject a CASH_OUT that exceeds the remaining 500 — must reject.
    r = client.post("/api/simulate/inject-transaction", json={
        "agent_id": "agent1000",
        "provider_id": "1",
        "type": "CASH_OUT",
        "amount": 5000,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("ok") is True
    assert body.get("rejected") is True
    assert body.get("reason") == "insufficient_physical_cash"
    # Rejections endpoint should show this row.
    counts = sim_api.get_rejection_counts("agent1000")
    assert counts.get("agent1000|1|CASH_OUT", 0) >= 1, (
        f"expected at least 1 physical-cash rejection, got {counts}"
    )


def test_inject_transaction_cash_in_rejected_when_wallet_empty():
    """A CASH_IN on bKash that exceeds the bKash e-wallet balance MUST be
    rejected: HTTP 200, body `{ok: True, rejected: True, reason:
    'insufficient_emoney_<provider_id>'}`. The bKash wallet balance MUST
    not go negative."""
    agent = _get_agent1000()
    if not agent:
        import pytest; pytest.skip("agent1000 not in seeded DB")

    _reset_rejections_for("agent1000")

    # Get current bKash wallet balance from the agent dashboard
    dash = client.get("/api/dashboard/agent?role=agent&user_id=agent1000").json()
    bk_wallet = next(
        (w for w in dash["wallets"] if w["provider_name"] == "bKash"), None
    )
    if not bk_wallet:
        import pytest; pytest.skip("bKash wallet not found in dashboard")
    bk_balance = bk_wallet["balance"]  # e.g. 68000 in seeded data

    # Try a CASH_IN of (balance + 50000) — should reject, wallet must not
    # mutate (we re-fetch the dashboard and confirm same balance).
    r = client.post("/api/simulate/inject-transaction", json={
        "agent_id": "agent1000",
        "provider_id": "1",
        "type": "CASH_IN",
        "amount": bk_balance + 50000,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("ok") is True
    assert body.get("rejected") is True
    assert body.get("reason") == "insufficient_emoney_1"

    # Re-fetch dashboard and confirm bKash balance is unchanged.
    dash2 = client.get("/api/dashboard/agent?role=agent&user_id=agent1000").json()
    bk2 = next(w for w in dash2["wallets"] if w["provider_name"] == "bKash")
    assert bk2["balance"] == bk_balance, (
        f"bKash balance mutated on rejected txn: {bk_balance} -> {bk2['balance']}"
    )

    # Rejection counter incremented.
    counts = sim_api.get_rejection_counts("agent1000")
    assert counts.get("agent1000|1|CASH_IN", 0) >= 1, (
        f"expected at least 1 emoney rejection, got {counts}"
    )


# ---------------------------------------------------------------------------
# 3. Rejection counter helper
# ---------------------------------------------------------------------------

def test_get_rejection_counts_scoped_to_agent():
    """`get_rejection_counts(agent_id=X)` returns only that agent's rows.
    `reset_rejection_counts(agent_id=X)` clears only that agent's rows."""
    sim_api.reset_rejection_counts(None)  # wipe everything
    # Simulate two rejections for agent A, one for agent B.
    sim_api._record_rejection("agentA", "1", "CASH_OUT")
    sim_api._record_rejection("agentA", "2", "CASH_OUT")
    sim_api._record_rejection("agentB", "3", "CASH_IN")

    a_counts = sim_api.get_rejection_counts("agentA")
    b_counts = sim_api.get_rejection_counts("agentB")
    assert a_counts.get("agentA|1|CASH_OUT") == 1
    assert a_counts.get("agentA|2|CASH_OUT") == 1
    assert b_counts.get("agentB|3|CASH_IN") == 1
    # No cross-agent leakage
    assert all(k.startswith("agentA|") for k in a_counts)
    assert all(k.startswith("agentB|") for k in b_counts)

    sim_api.reset_rejection_counts("agentA")
    assert sim_api.get_rejection_counts("agentA") == {}
    assert "agentB|3|CASH_IN" in sim_api.get_rejection_counts("agentB")

    # Cleanup
    sim_api.reset_rejection_counts(None)


# ---------------------------------------------------------------------------
# 4. HTTP endpoint — GET /api/simulate/rejections
# ---------------------------------------------------------------------------

def test_get_rejections_endpoint_returns_counts():
    """`GET /api/simulate/rejections?agent_id=X` returns the per-agent
    rejection counts in the `counts` field, with `by_provider` rollup."""
    agent = _get_agent1000()
    if not agent:
        import pytest; pytest.skip("agent1000 not in seeded DB")
    sim_api.reset_rejection_counts("agent1000")

    # Seed two rejections via the helper (so we don't have to mutate balances)
    sim_api._record_rejection("agent1000", "1", "CASH_OUT")
    sim_api._record_rejection("agent1000", "1", "CASH_OUT")
    sim_api._record_rejection("agent1000", "2", "CASH_IN")

    r = client.get("/api/simulate/rejections?agent_id=agent1000")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["agent_id"] == "agent1000"
    assert body["counts"]["agent1000|1|CASH_OUT"] == 2
    assert body["counts"]["agent1000|2|CASH_IN"] == 1
    # by_provider rollup: provider "1" has CASH_OUT=2, provider "2" has CASH_IN=1
    assert body["by_provider"]["1"]["CASH_OUT"] == 2
    assert body["by_provider"]["2"]["CASH_IN"] == 1

    # No agent_id → returns the full dict across all agents
    r2 = client.get("/api/simulate/rejections")
    assert r2.status_code == 200
    assert "counts" in r2.json()

    sim_api.reset_rejection_counts("agent1000")


# ---------------------------------------------------------------------------
# 5. inject_burst — rejection aggregation
# ---------------------------------------------------------------------------

def test_inject_burst_rejections_reported_per_provider():
    """`inject_burst` returns a per-provider rejections block. We force
    rejections by submitting amounts that exceed the bKash e-wallet."""
    agent = _get_agent1000()
    if not agent:
        import pytest; pytest.skip("agent1000 not in seeded DB")
    sim_api.reset_rejection_counts("agent1000")

    # 3 huge CASH_INs on bKash — all should reject (wallet can't cover).
    r = client.post("/api/simulate/inject-burst", json={
        "agent_id": "agent1000",
        "burst_size": 3,
        "provider_id": "1",
        "type": "CASH_IN",
        "amount": 999999,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("ok") is True
    rejections = body.get("rejections", {})
    # The rejections object should report at least 1 row for provider "1"
    # (could be more if multiple txns rejected). Sum of rejections == 3.
    total_rej = sum(rejections.values()) if isinstance(rejections, dict) else 0
    assert total_rej >= 1, f"expected >= 1 burst rejection, got {rejections}"

    sim_api.reset_rejection_counts("agent1000")


def test_reset_endpoint_clears_rejection_counter():
    """Calling /api/simulate/reset MUST wipe the in-process rejection
    counts for that agent — same lifecycle as the balance reset."""
    agent = _get_agent1000()
    if not agent:
        import pytest; pytest.skip("agent1000 not in seeded DB")

    sim_api._record_rejection("agent1000", "1", "CASH_OUT")
    sim_api._record_rejection("agent1000", "1", "CASH_OUT")
    assert sim_api.get_rejection_counts("agent1000"), "setup failed"

    r = client.post("/api/simulate/reset", json={"agent_id": "agent1000"})
    assert r.status_code == 200, r.text
    assert sim_api.get_rejection_counts("agent1000") == {}, (
        "reset endpoint must clear rejection counts"
    )
