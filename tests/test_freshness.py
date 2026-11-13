"""Direct-mode tests for ClaimFreshness.

The transaction date is pinned with vm.warp(), so every band boundary below is
an exact day count rather than a value that drifts with the wall clock.
"""

import json
from datetime import date, timedelta

SOURCE_URL = "https://research.example.org/report-42"

# Every test runs as if the transaction happened at this instant.
TODAY = date(2026, 6, 15)
WARP = "2026-06-15T12:00:00Z"


def _packed(d: date) -> int:
    return d.year * 10000 + d.month * 100 + d.day


def _shift(days_back: int) -> int:
    return _packed(TODAY - timedelta(days=days_back))


def _register(contract, **over):
    kwargs = dict(
        claim_id=1,
        statement="adoption reached 40% of surveyed teams in 2025",
        source_url=SOURCE_URL,
        published_on=_shift(10),
        window_days=90,
    )
    kwargs.update(over)
    return contract.register(**kwargs)


def _deploy(direct_deploy, direct_vm):
    direct_vm.warp(WARP)
    return direct_deploy("contracts/claim_freshness.py")


def _mock_source(vm, body: str):
    vm.mock_web(SOURCE_URL, {"status": 200, "body": body})


def _mock_date_answer(vm, answer: str):
    vm.mock_llm(r"The text below is the beginning of a web page", answer)


# ------------------------------------------------------------------ register

def test_register_accepts_a_sane_claim(direct_deploy, direct_vm):
    c = _deploy(direct_deploy, direct_vm)
    out = json.loads(_register(c))
    assert out["claim_id"] == 1
    assert out["state"] == "SUBMITTED"
    assert int(c.count_records()) == 1


def test_register_rejects_bad_input(direct_deploy, direct_vm):
    c = _deploy(direct_deploy, direct_vm)

    with direct_vm.expect_revert("already registered"):
        _register(c, claim_id=1)
        _register(c, claim_id=1)

    with direct_vm.expect_revert("statement is empty"):
        _register(c, claim_id=2, statement="   ")

    with direct_vm.expect_revert("must be http"):
        _register(c, claim_id=3, source_url="ftp://example.org/x")

    with direct_vm.expect_revert("real date"):
        _register(c, claim_id=4, published_on=20260230)

    with direct_vm.expect_revert("window_days must be"):
        _register(c, claim_id=5, window_days=0)

    with direct_vm.expect_revert("in the future"):
        _register(c, claim_id=6, published_on=_shift(-30))


# ---------------------------------------------------------------- fresh band

def test_fresh_inside_window_needs_no_network(direct_deploy, direct_vm):
    # No web or llm mock registered: a FRESH verdict must not touch the network.
    c = _deploy(direct_deploy, direct_vm)
    _register(c, published_on=_shift(10), window_days=90)
    out = json.loads(c.check(1))
    assert out["verdict"] == "FRESH"
    assert out["age_days"] == 10
    assert out["source_date"] == 0


def test_age_counts_whole_days(direct_deploy, direct_vm):
    c = _deploy(direct_deploy, direct_vm)

    _register(c, claim_id=1, published_on=_shift(0), window_days=90)
    assert json.loads(c.check(1))["age_days"] == 0

    _register(c, claim_id=2, published_on=_shift(1), window_days=90)
    assert json.loads(c.check(2))["age_days"] == 1

    _register(c, claim_id=3, published_on=_shift(89), window_days=90)
    assert json.loads(c.check(3))["age_days"] == 89


def test_boundary_stays_fresh(direct_deploy, direct_vm):
    c = _deploy(direct_deploy, direct_vm)
    _register(c, published_on=_shift(90), window_days=90)
    assert json.loads(c.check(1))["verdict"] == "FRESH"


def test_just_past_window_is_aging(direct_deploy, direct_vm):
    c = _deploy(direct_deploy, direct_vm)
    # window 90 -> grace = 90*5//4 = 112 days
    _register(c, claim_id=1, published_on=_shift(91), window_days=90)
    assert json.loads(c.check(1))["verdict"] == "AGING"

    _register(c, claim_id=2, published_on=_shift(112), window_days=90)
    assert json.loads(c.check(2))["verdict"] == "AGING"


def test_grace_band_is_computed_from_the_window(direct_deploy, direct_vm):
    c = _deploy(direct_deploy, direct_vm)
    _register(c, claim_id=1, published_on=_shift(91), window_days=90)
    assert json.loads(c.check(1))["grace_days"] == 112

    _register(c, claim_id=2, published_on=_shift(13), window_days=10)
    assert json.loads(c.check(2))["grace_days"] == 12


# ------------------------------------------------------------ past the band

def test_unreadable_source_is_unverifiable(direct_deploy, direct_vm):
    _mock_source(direct_vm, "<html><body>no dates here</body></html>")
    _mock_date_answer(direct_vm, "DATE: unknown")
    c = _deploy(direct_deploy, direct_vm)
    _register(c, published_on=_shift(400), window_days=30)
    out = json.loads(c.check(1))
    assert out["verdict"] == "UNVERIFIABLE"
    assert out["source_date"] == 0


def test_source_moved_means_superseded(direct_deploy, direct_vm):
    _mock_source(direct_vm, "<html>Last updated 2026-05-01</html>")
    _mock_date_answer(direct_vm, "DATE: 2026-05-01")
    c = _deploy(direct_deploy, direct_vm)
    # published 700 days back (2024-07-16); 2026-05-01 is later -> superseded
    _register(c, published_on=_shift(700), window_days=30)
    out = json.loads(c.check(1))
    assert out["verdict"] == "SUPERSEDED"
    assert out["source_date"] == 20260501


def test_source_unchanged_means_expired(direct_deploy, direct_vm):
    published = _shift(700)
    _mock_source(direct_vm, "<html>Published 2024-01-01</html>")
    _mock_date_answer(direct_vm, "DATE: 2024-01-01")
    c = _deploy(direct_deploy, direct_vm)
    _register(c, published_on=published, window_days=30)
    out = json.loads(c.check(1))
    # A date earlier than the claim's own publication is a bad read, not
    # movement, so it is clamped and the claim expires.
    assert out["verdict"] == "EXPIRED"
    assert out["source_date"] == published


def test_failed_fetch_is_unverifiable(direct_deploy, direct_vm):
    direct_vm.mock_web(SOURCE_URL, {"status": 500, "body": ""})
    _mock_date_answer(direct_vm, "DATE: unknown")
    c = _deploy(direct_deploy, direct_vm)
    _register(c, published_on=_shift(400), window_days=30)
    assert json.loads(c.check(1))["verdict"] == "UNVERIFIABLE"


# ------------------------------------------------------------------- records

def test_record_keeps_history(direct_deploy, direct_vm):
    c = _deploy(direct_deploy, direct_vm)
    _register(c, published_on=_shift(10), window_days=90)
    c.check(1)
    c.check(1)

    rec = json.loads(c.get_record(1))
    assert rec["state"] == "CHECKED"
    assert len(rec["history"]) == 2
    assert rec["history"][0]["verdict"] == "FRESH"
    assert rec["history"][1]["verdict"] == "FRESH"
    assert c.is_current(1) is True


def test_close_is_terminal(direct_deploy, direct_vm):
    c = _deploy(direct_deploy, direct_vm)
    _register(c)
    assert json.loads(c.close(1, "resolved"))["state"] == "CLOSED"
    with direct_vm.expect_revert("already closed"):
        c.close(1, "again")


def test_unknown_claim_reverts(direct_deploy, direct_vm):
    c = _deploy(direct_deploy, direct_vm)
    with direct_vm.expect_revert("not found"):
        c.check(99)
