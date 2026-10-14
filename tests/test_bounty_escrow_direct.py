"""Direct mode tests for SemanticBountyEscrow.

These run the GenVM runner in-process (leader only, no consensus) and mock all
non-deterministic web and LLM calls. They cover creation validation, access
control, the deterministic keyword gate, both settlement paths, live comparative
evaluation, resubmission, reclaim, double-payout protection and withdrawal.
"""

import json

import pytest


CONTRACT = "contracts/semantic_bounty_escrow.py"

TITLE = "Implement settlement module"
RUBRIC = "Clear module, tests, documented public methods"
KEYWORDS = "settlement,escrow,test"
GOOD_MANIFEST = (
    "This deliverable implements the settlement engine with escrow accounting "
    "and a full test suite."
)
GOOD_URL = "https://example.com/deliverable/1"


def _addr(account) -> str:
    """Return a hex address string from a direct-mode test address (raw bytes)."""
    if isinstance(account, (bytes, bytearray)):
        return "0x" + bytes(account).hex()
    if hasattr(account, "as_hex"):
        return account.as_hex
    return str(account)


def _create(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract = direct_deploy(CONTRACT)
    direct_vm.sender = direct_alice
    bounty_id = contract.create_bounty(
        _addr(direct_bob),
        TITLE,
        RUBRIC,
        KEYWORDS,
        75,
        1000,
    )
    return contract, int(bounty_id)


def _submit(direct_vm, contract, direct_bob, manifest=GOOD_MANIFEST, url=GOOD_URL):
    direct_vm.sender = direct_bob
    contract.submit_deliverable(1, url, manifest)


# ---------------------------------------------------------------------------
# Creation
# ---------------------------------------------------------------------------


def test_create_bounty_returns_incrementing_ids(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract = direct_deploy(CONTRACT)
    direct_vm.sender = direct_alice
    first = int(contract.create_bounty(_addr(direct_bob), TITLE, RUBRIC, KEYWORDS, 75, 1000))
    second = int(contract.create_bounty(_addr(direct_bob), TITLE, RUBRIC, KEYWORDS, 80, 500))
    assert first == 1
    assert second == 2
    stored = contract.get_bounty(first)
    assert stored["status"] == "OPEN"
    assert stored["sponsor"].lower() == _addr(direct_alice).lower()
    assert stored["worker"].lower() == _addr(direct_bob).lower()
    assert stored["min_pass_score"] == 75
    assert stored["escrow_amount"] == 1000
    assert stored["payout_released"] == 0


def test_create_rejects_self_dealing_sponsor_worker(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy(CONTRACT)
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("worker must differ from sponsor"):
        contract.create_bounty(_addr(direct_alice), TITLE, RUBRIC, KEYWORDS, 75, 1000)


def test_create_rejects_low_min_pass_score(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract = direct_deploy(CONTRACT)
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("min_pass_score must be between 50 and 100"):
        contract.create_bounty(_addr(direct_bob), TITLE, RUBRIC, KEYWORDS, 49, 1000)


def test_create_rejects_zero_escrow(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract = direct_deploy(CONTRACT)
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("escrow_amount must be greater than 0"):
        contract.create_bounty(_addr(direct_bob), TITLE, RUBRIC, KEYWORDS, 75, 0)


def test_create_rejects_empty_title(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract = direct_deploy(CONTRACT)
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("Title must not be empty"):
        contract.create_bounty(_addr(direct_bob), "   ", RUBRIC, KEYWORDS, 75, 1000)


def test_create_rejects_invalid_worker_address(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy(CONTRACT)
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("Invalid address"):
        contract.create_bounty("not-an-address", TITLE, RUBRIC, KEYWORDS, 75, 1000)


# ---------------------------------------------------------------------------
# Submission and the deterministic keyword gate
# ---------------------------------------------------------------------------


def test_submit_rejects_unauthorized_worker(direct_vm, direct_deploy, direct_alice, direct_bob, direct_charlie):
    contract, bounty_id = _create(direct_vm, direct_deploy, direct_alice, direct_bob)
    direct_vm.sender = direct_charlie
    with direct_vm.expect_revert("Only the assigned worker may submit"):
        contract.submit_deliverable(bounty_id, GOOD_URL, GOOD_MANIFEST)


def test_submit_rejects_missing_keyword(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract, bounty_id = _create(direct_vm, direct_deploy, direct_alice, direct_bob)
    partial = "Only talks about escrow accounting, nothing else."
    direct_vm.sender = direct_bob
    with direct_vm.expect_revert("Missing mandatory keywords"):
        contract.submit_deliverable(bounty_id, GOOD_URL, partial)


def test_submit_rejects_invalid_url(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract, bounty_id = _create(direct_vm, direct_deploy, direct_alice, direct_bob)
    direct_vm.sender = direct_bob
    with direct_vm.expect_revert("deliverable_url must start with http"):
        contract.submit_deliverable(bounty_id, "ftp://example.com", GOOD_MANIFEST)


def test_submit_rejects_empty_manifest(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract, bounty_id = _create(direct_vm, direct_deploy, direct_alice, direct_bob)
    direct_vm.sender = direct_bob
    with direct_vm.expect_revert("deliverable_manifest must not be empty"):
        contract.submit_deliverable(bounty_id, GOOD_URL, "   ")


def test_submit_valid_sets_submitted(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract, bounty_id = _create(direct_vm, direct_deploy, direct_alice, direct_bob)
    _submit(direct_vm, contract, direct_bob)
    stored = contract.get_bounty(bounty_id)
    assert stored["status"] == "SUBMITTED"
    assert stored["deliverable_url"] == GOOD_URL


# ---------------------------------------------------------------------------
# Deterministic settlement by the sponsor
# ---------------------------------------------------------------------------


def test_deterministic_settlement_pass(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract, bounty_id = _create(direct_vm, direct_deploy, direct_alice, direct_bob)
    _submit(direct_vm, contract, direct_bob)
    direct_vm.sender = direct_alice
    verdict = contract.approve_deterministic_settlement(bounty_id, 90, "Excellent work")
    assert verdict == "SETTLED_PAID"
    stored = contract.get_bounty(bounty_id)
    assert stored["status"] == "SETTLED_PAID"
    assert stored["achieved_score"] == 90
    assert stored["payout_released"] == 1000
    assert int(contract.get_claimable_payout(_addr(direct_bob))) == 1000


def test_deterministic_settlement_reject(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract, bounty_id = _create(direct_vm, direct_deploy, direct_alice, direct_bob)
    _submit(direct_vm, contract, direct_bob)
    direct_vm.sender = direct_alice
    verdict = contract.approve_deterministic_settlement(bounty_id, 60, "Below bar")
    assert verdict == "REJECTED"
    stored = contract.get_bounty(bounty_id)
    assert stored["status"] == "REJECTED"
    assert stored["payout_released"] == 0
    assert int(contract.get_claimable_payout(_addr(direct_bob))) == 0


def test_deterministic_settlement_rejects_score_out_of_range(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract, bounty_id = _create(direct_vm, direct_deploy, direct_alice, direct_bob)
    _submit(direct_vm, contract, direct_bob)
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("verified_score must be between 0 and 100"):
        contract.approve_deterministic_settlement(bounty_id, 140, "Invalid")


def test_deterministic_settlement_only_sponsor(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract, bounty_id = _create(direct_vm, direct_deploy, direct_alice, direct_bob)
    _submit(direct_vm, contract, direct_bob)
    direct_vm.sender = direct_bob
    with direct_vm.expect_revert("Only the sponsor may settle deterministically"):
        contract.approve_deterministic_settlement(bounty_id, 90, "x")


def test_double_settlement_blocked(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract, bounty_id = _create(direct_vm, direct_deploy, direct_alice, direct_bob)
    _submit(direct_vm, contract, direct_bob)
    direct_vm.sender = direct_alice
    contract.approve_deterministic_settlement(bounty_id, 90, "first")
    with direct_vm.expect_revert("Bounty must be SUBMITTED to settle"):
        contract.approve_deterministic_settlement(bounty_id, 95, "second")
    # Payout must not have been credited twice.
    assert int(contract.get_claimable_payout(_addr(direct_bob))) == 1000


# ---------------------------------------------------------------------------
# Live comparative evaluation
# ---------------------------------------------------------------------------


def test_live_evaluation_pass(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract, bounty_id = _create(direct_vm, direct_deploy, direct_alice, direct_bob)
    _submit(direct_vm, contract, direct_bob)
    direct_vm.mock_web(
        r".*example\.com/deliverable.*",
        {"status": 200, "body": "Full settlement escrow test implementation."},
    )
    direct_vm.mock_llm(
        r".*deliverable evaluator.*",
        json.dumps(
            {
                "score": 92,
                "all_mandatory_keywords_present": True,
                "verdict": "PASS",
                "summary": "Meets rubric",
            }
        ),
    )
    verdict = contract.evaluate_deliverable_live(bounty_id)
    assert verdict == "SETTLED_PAID"
    stored = contract.get_bounty(bounty_id)
    assert stored["achieved_score"] == 92
    assert int(contract.get_claimable_payout(_addr(direct_bob))) == 1000


def test_live_evaluation_low_score_rejects(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract, bounty_id = _create(direct_vm, direct_deploy, direct_alice, direct_bob)
    _submit(direct_vm, contract, direct_bob)
    direct_vm.mock_web(
        r".*example\.com/deliverable.*",
        {"status": 200, "body": "settlement escrow test present but weak"},
    )
    direct_vm.mock_llm(
        r".*deliverable evaluator.*",
        json.dumps(
            {
                "score": 40,
                "all_mandatory_keywords_present": True,
                "verdict": "FAIL",
                "summary": "Below bar",
            }
        ),
    )
    verdict = contract.evaluate_deliverable_live(bounty_id)
    assert verdict == "REJECTED"
    assert int(contract.get_claimable_payout(_addr(direct_bob))) == 0


def test_live_evaluation_missing_keyword_overrides_hallucinated_pass(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    # Leader LLM claims all keywords present with a high score, but the fetched
    # page text does not actually contain a mandatory keyword. The deterministic
    # gate inside the evaluator must flip the flag and reject the payout.
    contract, bounty_id = _create(direct_vm, direct_deploy, direct_alice, direct_bob)
    _submit(direct_vm, contract, direct_bob)
    direct_vm.mock_web(
        r".*example\.com/deliverable.*",
        {"status": 200, "body": "settlement and escrow only, the required token is absent"},
    )
    direct_vm.mock_llm(
        r".*deliverable evaluator.*",
        json.dumps(
            {
                "score": 99,
                "all_mandatory_keywords_present": True,
                "verdict": "PASS",
                "summary": "Hallucinated pass",
            }
        ),
    )
    verdict = contract.evaluate_deliverable_live(bounty_id)
    assert verdict == "REJECTED"
    assert int(contract.get_claimable_payout(_addr(direct_bob))) == 0


# ---------------------------------------------------------------------------
# Resubmission, reclaim, withdrawal
# ---------------------------------------------------------------------------


def test_resubmission_after_rejected(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract, bounty_id = _create(direct_vm, direct_deploy, direct_alice, direct_bob)
    _submit(direct_vm, contract, direct_bob)
    direct_vm.sender = direct_alice
    contract.approve_deterministic_settlement(bounty_id, 30, "weak")
    assert contract.get_bounty(bounty_id)["status"] == "REJECTED"
    _submit(direct_vm, contract, direct_bob)
    assert contract.get_bounty(bounty_id)["status"] == "SUBMITTED"


def test_reclaim_open(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract, bounty_id = _create(direct_vm, direct_deploy, direct_alice, direct_bob)
    direct_vm.sender = direct_alice
    reclaimed = int(contract.reclaim_bounty(bounty_id))
    assert reclaimed == 1000
    assert contract.get_bounty(bounty_id)["status"] == "REFUNDED"
    assert int(contract.get_claimable_payout(_addr(direct_alice))) == 1000


def test_reclaim_after_rejected(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract, bounty_id = _create(direct_vm, direct_deploy, direct_alice, direct_bob)
    _submit(direct_vm, contract, direct_bob)
    direct_vm.sender = direct_alice
    contract.approve_deterministic_settlement(bounty_id, 20, "weak")
    reclaimed = int(contract.reclaim_bounty(bounty_id))
    assert reclaimed == 1000
    assert contract.get_bounty(bounty_id)["status"] == "REFUNDED"


def test_reclaim_blocked_on_submitted(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract, bounty_id = _create(direct_vm, direct_deploy, direct_alice, direct_bob)
    _submit(direct_vm, contract, direct_bob)
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("Only OPEN or REJECTED bounties may be reclaimed"):
        contract.reclaim_bounty(bounty_id)


def test_reclaim_blocked_on_settled_paid(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract, bounty_id = _create(direct_vm, direct_deploy, direct_alice, direct_bob)
    _submit(direct_vm, contract, direct_bob)
    direct_vm.sender = direct_alice
    contract.approve_deterministic_settlement(bounty_id, 90, "good")
    with direct_vm.expect_revert("Only OPEN or REJECTED bounties may be reclaimed"):
        contract.reclaim_bounty(bounty_id)


def test_withdraw_payout(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract, bounty_id = _create(direct_vm, direct_deploy, direct_alice, direct_bob)
    _submit(direct_vm, contract, direct_bob)
    direct_vm.sender = direct_alice
    contract.approve_deterministic_settlement(bounty_id, 88, "good")
    direct_vm.sender = direct_bob
    amount = int(contract.withdraw_payout())
    assert amount == 1000
    assert int(contract.get_claimable_payout(_addr(direct_bob))) == 0


def test_withdraw_payout_reverts_when_empty(direct_vm, direct_deploy, direct_charlie):
    contract = direct_deploy(CONTRACT)
    direct_vm.sender = direct_charlie
    with direct_vm.expect_revert("No claimable payout to withdraw"):
        contract.withdraw_payout()


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------


def test_escrow_summary_and_actor_indexes(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract, bounty_id = _create(direct_vm, direct_deploy, direct_alice, direct_bob)
    _submit(direct_vm, contract, direct_bob)
    direct_vm.sender = direct_alice
    contract.approve_deterministic_settlement(bounty_id, 91, "good")

    summary = contract.get_escrow_summary()
    assert summary["total_bounties"] == 1
    assert summary["settled_paid"] == 1
    assert summary["total_escrow_locked"] == 0
    assert summary["total_payout_released"] == 1000

    sponsor_view = contract.get_actor_bounties(_addr(direct_alice))
    worker_view = contract.get_actor_bounties(_addr(direct_bob))
    assert sponsor_view["sponsor"] == [1]
    assert worker_view["worker"] == [1]
