# SemanticBountyEscrow

A standalone, reusable GenLayer Intelligent Contract primitive: an automated
milestone-based bounty escrow and deliverable verification engine for
decentralized work, technical bounties, and agent-to-agent task settlement.

It has no frontend by design. The contract owns the minimum state transitions
that need validator consensus: the escrow lifecycle, the deterministic keyword
gate, the rubric settlement decision, and the payout accounting.

- Runner: pinned `py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6`
- Network: `studionet` (chain id 61999)
- Deployed contract address: `0x6C972C4AEC5872E4089bc20849218B6402cecf02`
- Source sha256 (local and on-chain, byte-for-byte identical):
  `b56bf397f2165c7e3c76b91bbdaa7db50cc83b5dca81c290b57a9fc53a471fb3`

## Why GenLayer

A bounty settlement is a subjective judgment (does this deliverable meet the
rubric?) that has a direct effect on who gets paid. That is exactly the kind of
decision multiple validators should verify independently rather than trust one
off-chain oracle. The contract couples the AI-mediated judgment to an on-chain,
appealable state transition and a fund-moving effect.

## Escrow state machine

| Status | Meaning | Exits |
|--------|---------|-------|
| `OPEN` | Escrowed, no deliverable yet | worker `submit_deliverable` -> `SUBMITTED`; sponsor `reclaim_bounty` -> `REFUNDED` |
| `SUBMITTED` | A deliverable awaits settlement | sponsor `approve_deterministic_settlement` or anyone `evaluate_deliverable_live` -> `SETTLED_PAID` or `REJECTED` |
| `REJECTED` | Scored below the bar, escrow still locked | worker `submit_deliverable` -> `SUBMITTED` (resubmission); sponsor `reclaim_bounty` -> `REFUNDED` |
| `SETTLED_PAID` | Terminal, worker credited | none |
| `REFUNDED` | Terminal, sponsor credited | none |

While a bounty is `SUBMITTED`, the sponsor is deliberately blocked from
`reclaim_bounty`, so it cannot grab the money out from under a pending review.

## Two-stage verification

1. Deterministic keyword gate. On `submit_deliverable`, if the bounty declares
   `required_keywords_csv`, every comma-separated token must appear
   case-insensitively in the submitted manifest, otherwise the transaction
   reverts. Obviously incomplete work never enters the settlement path.
2. Rubric evaluation under consensus. `evaluate_deliverable_live` renders the
   `deliverable_url`, scores the deliverable against `requirements_rubric` with
   an LLM, and wraps the whole fetch-and-score in
   `gl.eq_principle.prompt_comparative` so validators independently re-fetch the
   URL and must agree on the pass/fail verdict and on the score band relative to
   `min_pass_score`. The keyword gate is then re-applied to the actually fetched
   text, so a hallucinated `all_mandatory_keywords_present` flag cannot by itself
   unlock a payout. The score is clamped to `0..100` on every path.

## Public interface

Writes:
- `create_bounty(worker, title, requirements_rubric, required_keywords_csv, min_pass_score, escrow_amount) -> bounty_id`
- `submit_deliverable(bounty_id, deliverable_url, deliverable_manifest)`
- `approve_deterministic_settlement(bounty_id, verified_score, summary) -> verdict`
- `evaluate_deliverable_live(bounty_id) -> verdict`
- `reclaim_bounty(bounty_id) -> reclaimed_amount`
- `withdraw_payout() -> withdrawn_amount`

Views:
- `get_bounty(bounty_id) -> dict`
- `get_claimable_payout(account) -> amount`
- `get_escrow_summary() -> dict`
- `get_actor_bounties(account) -> dict`

## Escrow conservation invariant

Each bounty `escrow_amount` is credited at most once, to either the worker
(`SETTLED_PAID`) or the sponsor (`REFUNDED`), never to both and never twice. The
only credit paths are `approve_deterministic_settlement` (pass), `evaluate_deliverable_live`
(pass) and `reclaim_bounty`, and all of them push the bounty to a terminal state
that rejects further credits. `withdraw_payout` zeroes the ledger balance and
reverts on an empty one. See `docs/adversarial-review.md` for the full attack
analysis and the tests that prove each guard.

## Verification status

- `genvm-lint check`: 0 errors (the only message is an informational note that a
  newer runner exists; the pinned hash is kept deliberately as required).
- Direct-mode tests: `tests/test_bounty_escrow_direct.py`, 27 tests, all passing.
- Studionet integration test: `tests/test_bounty_escrow_integration.py`, passing
  against the live network (run with `STUDIONET_RUN=1 gltest ...`).

Studionet transaction hashes from the recorded run (all `FINALIZED`):

| Step | Transaction |
|------|-------------|
| deploy | `0x66f6bcd1527e6732aaa401bf02a2b17c1f50159d0ec020e451ad02aa9ec564b7` |
| create_bounty | `0x7e5745de0a40eb790579aa972d1e2338e82c5e3fc5e54c468791affa44c6e0d6` |
| submit_deliverable | `0x18cda68c7d1a3ff4d64b7e264894e15d2a6d07ca45d24db925b82cce15325976` |
| approve_deterministic_settlement | `0x2f88647b486d75df996c5e2625fe0c951291d0bb49d5f8736f405b4d0406f7a5` |
| create_bounty (second) | `0x7696ef0c1ab41a9e133d4d7dffcb8a2f520d7b0774676f76b6c575a087949354` |
| submit_deliverable (unauthorized, expected no-op) | `0x49cc93ee23d45b63a9cbd9977820b4e6833fd70bd601cac1ec1609ac64678a35` |

The full machine-readable record, including final state and the sha256 identity
check, is in `evidence/studionet_deployment.json`.

### On how the deployed code was verified

`gen_getContractCode` on studionet returns the stored source base64-encoded. The
deploy driver base64-decodes it and compares sha256 against the local file; the
result is byte-for-byte identical (`sha256_identity: true` in the evidence). The
hosted block explorer `genlayer-explorer.vercel.app` was returning `503
DEPLOYMENT_PAUSED` during this session, so the deploy transactions were verified
through the on-chain consensus-data contract (the same source the explorer reads)
reached via `client.get_transaction`, which confirmed `FINALIZED` status for every
transaction. This is recorded honestly in the evidence file rather than claimed as
an explorer-confirmed result.

## Limitations (read before using)

- This contract is a settlement ledger, not a GLX vault. `create_bounty` is not
  payable and moves no native value, and `withdraw_payout` debits an internal
  ledger rather than sending native tokens. An integrator must custody and move
  the real GLX outside this primitive; the conservation invariant is proven over
  the ledger.
- The deterministic path (`approve_deterministic_settlement`) trusts the sponsor
  as the score authority. A malicious sponsor can pay or reject at will on that
  path. Use `evaluate_deliverable_live` when the settlement decision must be
  validator-consensed rather than sponsor-decided.
- There is no on-chain deadline. Recovery relies on role- and status-gated exits
  (sponsor `reclaim_bounty`, or reject-then-reclaim), not on a timer.
- `evaluate_deliverable_live` reverts on unreachable evidence instead of
  auto-refunding; the sponsor keeps a manual refund exit, but the live path does
  not fail closed to refund by itself.
- Free-text fields have no upper length caps. Deployers should add byte limits at
  the boundary for production.
- Direct-mode tests run only the leader path. They prove the state machine and
  guards, not validator agreement under a hostile page; that requires live
  studionet runs and is not fully covered for adversarial content here.

## Layout

```
contracts/semantic_bounty_escrow.py      the primitive
tests/test_bounty_escrow_direct.py       27 direct-mode tests
tests/test_bounty_escrow_integration.py  opt-in live studionet test
scripts/deploy_studionet.py              deploy, drive, verify, write evidence
docs/adversarial-review.md               attack analysis and findings
evidence/studionet_deployment.json       on-chain proof and sha256 identity
.env.example                             credential template (never commit .env)
```

## Setup

Fill `.env` from `.env.example` with `GENLAYER_PRIVATE_KEY` and `GITHUB_TOKEN`.
The real `.env` is gitignored and must never be committed.

```
genvm-lint check contracts/semantic_bounty_escrow.py
gltest tests/test_bounty_escrow_direct.py -v -s
STUDIONET_RUN=1 gltest tests/test_bounty_escrow_integration.py -v -s
python scripts/deploy_studionet.py
```
