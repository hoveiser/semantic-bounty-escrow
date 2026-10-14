"""Studionet integration test for SemanticBountyEscrow.

This exercises the real consensus network: it deploys the contract and drives a
multi-account escrow scenario with full leader plus validator agreement, then
asserts the resulting on-chain state and the byte-for-byte sha256 identity of the
deployed code.

Each run submits live transactions and therefore costs a fresh deployment, so it
is opt-in. Run it with:

    STUDIONET_RUN=1 gltest tests/test_bounty_escrow_integration.py -v -s

Without the flag the test is skipped so that ordinary direct-mode CI stays fast
and offline.
"""

import os

import pytest


@pytest.mark.skipif(
    os.environ.get("STUDIONET_RUN") != "1",
    reason="Set STUDIONET_RUN=1 to drive live studionet transactions",
)
def test_studionet_escrow_scenario():
    import sys
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo_root / "scripts"))

    from deploy_studionet import run_scenario  # noqa: E402

    result = run_scenario()

    # Consensus and lifecycle.
    assert result["network"] == "studionet"
    assert result["chain_id"] == 61999
    assert result["contract_address"]
    for tx in result["transactions"]:
        assert tx["reached_finalized"] is True, tx
        assert tx["status"] == "FINALIZED", tx

    # Deployed code is byte-for-byte the local source.
    assert result["sha256_identity"] is True, result

    # Deterministic settlement happy path paid the worker exactly one escrow.
    settled = result["final_state"]["settled_bounty"]
    assert settled["status"] == "SETTLED_PAID"
    assert int(settled["achieved_score"]) == 90
    assert int(settled["payout_released"]) == int(settled["escrow_amount"])
    assert int(result["final_state"]["worker_claimable"]) == int(
        settled["escrow_amount"]
    )

    # An unauthorized submission finalized but changed nothing.
    assert result["final_state"]["unauthorized_attempt_bounty_status"] == "OPEN"

    # Accounting invariant from the summary view: exactly one bounty paid out and
    # one still locked, nothing double counted.
    summary = result["final_state"]["escrow_summary"]
    assert summary["settled_paid"] == 1
    assert summary["total_payout_released"] == summary["total_escrow_locked"]
