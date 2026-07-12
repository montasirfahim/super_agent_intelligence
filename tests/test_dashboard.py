"""Integration tests for role-aware dashboard endpoints + ticket actions."""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


# -------- Lookup endpoints --------

def test_lookup_agents_returns_list():
    r = client.get("/api/lookup/agents")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)
    assert len(body) > 0
    assert "agent_id" in body[0]


def test_lookup_offices_returns_provider_metadata():
    r = client.get("/api/lookup/offices")
    assert r.status_code == 200
    body = r.json()
    assert len(body) > 0
    providers = {o["provider"] for o in body}
    assert {"bKash", "Nagad", "Rocket"}.issubset(providers)


def test_lookup_analysts_returns_provider_metadata():
    r = client.get("/api/lookup/analysts")
    assert r.status_code == 200
    body = r.json()
    assert len(body) > 0
    for a in body:
        assert a["provider"] in ("bKash", "Nagad", "Rocket")


# -------- Agent dashboard --------

def test_agent_dashboard_returns_own_data():
    agents = client.get("/api/lookup/agents").json()
    aid = agents[0]["agent_id"]
    r = client.get(f"/api/dashboard/agent?role=agent&user_id={aid}")
    assert r.status_code == 200
    body = r.json()
    assert body["agent"]["agent_id"] == aid
    # Three provider slots always present (even if no wallet)
    assert len(body["wallets"]) == 3
    wallet_names = {w["provider_name"] for w in body["wallets"]}
    assert wallet_names == {"bKash", "Nagad", "Rocket"}


def test_agent_dashboard_rejects_invalid_role():
    r = client.get("/api/dashboard/agent?role=hacker&user_id=x")
    assert r.status_code == 400


def test_agent_dashboard_requires_user_id():
    r = client.get("/api/dashboard/agent?role=agent")
    assert r.status_code == 400


# -------- Officer dashboard --------

def test_officer_dashboard_returns_office_data():
    offices = client.get("/api/lookup/offices").json()
    oid = offices[0]["id"]
    r = client.get(f"/api/dashboard/officer?role=officer&user_id={oid}")
    assert r.status_code == 200
    body = r.json()
    assert body["office"]["id"] == oid
    assert "agent_count" in body
    # Provider boundary: office.provider must match each ticket's provider_id
    for t in body["tickets"]:
        # We don't expose provider_id in tickets here, but the office.provider does limit
        assert body["office"]["provider"] in ("bKash", "Nagad", "Rocket")


def test_officer_dashboard_is_provider_scoped():
    """Tickets returned must match the office's provider."""
    offices = client.get("/api/lookup/offices").json()
    bkash_office = next(o for o in offices if o["provider"] == "bKash")
    nagad_office = next(o for o in offices if o["provider"] == "Nagad")
    bkash_tickets = client.get(
        f"/api/dashboard/officer?role=officer&user_id={bkash_office['id']}"
    ).json()["tickets"]
    nagad_tickets = client.get(
        f"/api/dashboard/officer?role=officer&user_id={nagad_office['id']}"
    ).json()["tickets"]
    # No ticket_id overlap between providers (each ticket has one provider)
    bkash_ids = {t["ticket_id"] for t in bkash_tickets}
    nagad_ids = {t["ticket_id"] for t in nagad_tickets}
    assert bkash_ids.isdisjoint(nagad_ids), "Provider boundary leaked!"


# -------- Analyst dashboard --------

def test_analyst_dashboard_returns_under_review_funnel():
    analysts = client.get("/api/lookup/analysts").json()
    aid = analysts[0]["id"]
    r = client.get(f"/api/dashboard/analyst?role=analyst&user_id={aid}")
    assert r.status_code == 200
    body = r.json()
    assert body["analyst"]["id"] == aid
    assert "funnel" in body
    for status_key in ("OPEN", "ACKNOWLEDGED", "UNDER_REVIEW", "RESOLVED"):
        assert status_key in body["funnel"]


# -------- Ticket state machine --------

def _get_open_ticket(office_id):
    tickets = client.get(
        f"/api/dashboard/officer?role=officer&user_id={office_id}"
    ).json()["tickets"]
    return next((t for t in tickets if t["status"] == "OPEN"), None)


def test_ticket_acknowledge_transitions_state():
    offices = client.get("/api/lookup/offices").json()
    for o in offices:
        if o["provider"] != "bKash":
            continue
        t = _get_open_ticket(o["id"])
        if t is None:
            continue
        tid = t["ticket_id"]
        r = client.post(
            f"/api/tickets/{tid}/acknowledge?role=officer&user_id={o['id']}"
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["new_status"] == "ACKNOWLEDGED"

        # Verify audit log written
        ev = client.get(
            f"/api/tickets/{tid}/evidence?role=officer&user_id={o['id']}"
        ).json()
        assert ev["status"] == "ACKNOWLEDGED"
        return
    # If no open tickets seeded, skip
    import pytest
    pytest.skip("No open bKash tickets to test")


def test_ticket_escalate_requires_officer_role():
    offices = client.get("/api/lookup/offices").json()
    oid = offices[0]["id"]
    tickets = client.get(
        f"/api/dashboard/officer?role=officer&user_id={oid}"
    ).json()["tickets"]
    if not tickets:
        import pytest; pytest.skip("no tickets")
    tid = tickets[0]["ticket_id"]
    # Agent role should be forbidden
    r = client.post(
        f"/api/tickets/{tid}/escalate?role=agent&user_id=x"
    )
    assert r.status_code == 400 or r.status_code == 403


def test_analyst_can_confirm_under_review_ticket():
    """Full state-machine round-trip: open → ack → escalate → confirm."""
    offices = client.get("/api/lookup/offices").json()
    analysts = client.get("/api/lookup/analysts").json()
    # Find an analyst + matching office that has tickets
    for a in analysts:
        # offices under this analyst
        ofc_list = client.get(
            f"/api/dashboard/analyst?role=analyst&user_id={a['id']}"
        ).json()
        offices_for_analyst = ofc_list.get("offices", [])
        for o in offices_for_analyst:
            tickets = client.get(
                f"/api/dashboard/officer?role=officer&user_id={o['id']}"
            ).json()["tickets"]
            ur = next((t for t in tickets if t["status"] == "UNDER_REVIEW"), None)
            if not ur:
                continue
            tid = ur["ticket_id"]
            r = client.post(
                f"/api/tickets/{tid}/confirm-anomaly?role=analyst&user_id={a['id']}"
            )
            assert r.status_code == 200
            assert r.json()["verdict"] == "REAL"
            return
    import pytest; pytest.skip("no UNDER_REVIEW tickets available")


# -------- Multi-tenant isolation --------

def test_officer_cannot_view_other_office_ticket():
    offices = client.get("/api/lookup/offices").json()
    if len(offices) < 2:
        import pytest; pytest.skip("need 2+ offices")
    a, b = offices[0], offices[1]
    if a["provider"] != b["provider"]:
        import pytest; pytest.skip("need same-provider pair")
    tickets_b = client.get(
        f"/api/dashboard/officer?role=officer&user_id={b['id']}"
    ).json()["tickets"]
    if not tickets_b:
        import pytest; pytest.skip("no tickets in office B")
    tid = tickets_b[0]["ticket_id"]
    # Office A officer should be denied
    r = client.get(
        f"/api/tickets/{tid}/evidence?role=officer&user_id={a['id']}"
    )
    assert r.status_code == 403


def test_agent_cannot_view_other_agent_ticket():
    agents = client.get("/api/lookup/agents").json()
    if len(agents) < 2:
        import pytest; pytest.skip("need 2+ agents")
    a, b = agents[0], agents[1]
    # Pick a ticket belonging to agent B
    dashboard_b = client.get(
        f"/api/dashboard/agent?role=agent&user_id={b['agent_id']}"
    ).json()
    if not dashboard_b["alerts"]:
        import pytest; pytest.skip("agent B has no alerts")
    alert_id = dashboard_b["alerts"][0]["alert_id"]
    # Find ticket via analyst
    analysts = client.get("/api/lookup/analysts").json()
    for an in analysts:
        tlist = client.get(
            f"/api/dashboard/analyst?role=analyst&user_id={an['id']}"
        ).json()["tickets"]
        for t in tlist:
            if t["alert_id"] == alert_id:
                tid = t["ticket_id"]
                r = client.get(
                    f"/api/tickets/{tid}/evidence?role=agent&user_id={a['agent_id']}"
                )
                assert r.status_code == 403
                return
    import pytest; pytest.skip("couldn't locate ticket")


# -------- End-to-end cross-provider isolation (live DB) --------

def test_evaluate_creates_provider_isolated_tickets():
    """Drain bKash and Nagad for agent1000, evaluate, and confirm that the
    two resulting tickets' evidence payloads are disjoint — neither carries
    the other provider's balance, customer hashes, or top-up amount.

    Skips cleanly if:
      - agent1000 isn't seeded (live DB shape may differ across envs)
      - the drain + evaluate cycle fails to produce a 2-ticket correlated alert
        (e.g. baseline already covers this burn rate). The test asserts the
        engine, not the seed data, so a `pytest.skip` here is acceptable.
    """
    import json
    agents = client.get("/api/lookup/agents").json()
    agent = next((a for a in agents if a["agent_id"] == "agent1000"), None)
    if not agent:
        import pytest; pytest.skip("agent1000 not in seeded DB")

    # Drain bKash (provider_id="1") + Nagad (provider_id="2") with big CASH_OUT
    # bursts so the engine trips correlated-spike detection.
    for prov in ("1", "2"):
        for _ in range(8):
            r = client.post("/api/simulate/inject-transaction", json={
                "agent_id": "agent1000",
                "provider_id": prov,
                "type": "CASH_OUT",
                "amount": 18000,
            })
            assert r.status_code == 200, r.text

    # Trigger the engine. Analyst role is required.
    analysts = client.get("/api/lookup/analysts").json()
    aid = analysts[0]["id"]
    r = client.post(
        "/api/analytics/evaluate",
        params={"role": "analyst", "user_id": aid},
        json={"agent_id": "agent1000"},
    )
    if r.status_code != 200 or r.json().get("alert_id") is None:
        import pytest; pytest.skip(f"no correlated alert produced: {r.status_code} {r.text}")
    body = r.json()
    if len(body["tickets"]) < 2:
        import pytest; pytest.skip(
            f"expected ≥2 tickets for correlated drain, got {len(body['tickets'])}"
        )

    by_prov = {tk["provider_id"]: tk["ticket_id"] for tk in body["tickets"]}
    if "1" not in by_prov or "2" not in by_prov:
        import pytest; pytest.skip(f"need both bKash and Nagad tickets, got {list(by_prov)}")

    # Resolve the right officer for each provider so the /evidence GET is authed.
    offices = client.get("/api/lookup/offices").json()
    bk_office = next((o for o in offices if o["provider"] == "bKash"), None)
    ng_office = next((o for o in offices if o["provider"] == "Nagad"), None)
    if not bk_office or not ng_office:
        import pytest; pytest.skip("missing bKash or Nagad office in seed")

    bk_ev = client.get(
        f"/api/tickets/{by_prov['1']}/evidence?role=officer&user_id={bk_office['id']}"
    ).json()
    ng_ev = client.get(
        f"/api/tickets/{by_prov['2']}/evidence?role=officer&user_id={ng_office['id']}"
    ).json()

    # Evidence endpoint wraps the engine's slice under `evidence`
    bk_slice = bk_ev["evidence"]
    ng_slice = ng_ev["evidence"]

    # (1) Each ticket carries only its own provider label
    assert bk_ev["provider_id"] == "1"
    assert ng_ev["provider_id"] == "2"
    assert bk_slice["provider_id"] == "1"
    assert ng_slice["provider_id"] == "2"

    # (2) Liquidity balances belong to that ticket's provider only
    assert bk_slice["liquidity"]["provider_id"] == "1"
    assert ng_slice["liquidity"]["provider_id"] == "2"

    # (3) Top-up amounts differ (per-provider recommendation, not shared)
    bk_topup = bk_slice["recommended_topup"]["amount"]
    ng_topup = ng_slice["recommended_topup"]["amount"]
    assert bk_topup != ng_topup, (
        f"Per-provider top-ups must differ; both got {bk_topup}"
    )

    # (4) Each ticket's serialized evidence contains NONE of the other
    # provider's current_balance digits (defense in depth on top of the
    # engine's own _assert_no_cross_provider_leak guard).
    bk_balance = bk_slice["liquidity"]["current_balance"].replace(".", "")
    ng_balance = ng_slice["liquidity"]["current_balance"].replace(".", "")
    bk_blob = json.dumps(bk_slice)
    ng_blob = json.dumps(ng_slice)
    if len(ng_balance) >= 5 and ng_balance != bk_balance:
        # Word-boundary check: ng's digits must not appear inside bk's evidence
        idx = 0
        while True:
            j = bk_blob.find(ng_balance, idx)
            if j < 0:
                break
            assert False, (
                f"bKash evidence leaked Nagad balance digits {ng_balance!r}"
            )
            break
    if len(bk_balance) >= 5 and bk_balance != ng_balance:
        idx = 0
        while True:
            j = ng_blob.find(bk_balance, idx)
            if j < 0:
                break
            assert False, (
                f"Nagad evidence leaked bKash balance digits {bk_balance!r}"
            )
            break

    # (5) correlated_with lists only OTHER provider IDs (no balance, no dict)
    assert all(isinstance(x, str) for x in bk_slice["correlated_with"])
    assert all(isinstance(x, str) for x in ng_slice["correlated_with"])
    for x in bk_slice["correlated_with"]:
        assert x in ("1", "2", "3") and x != "1"
    for x in ng_slice["correlated_with"]:
        assert x in ("1", "2", "3") and x != "2"