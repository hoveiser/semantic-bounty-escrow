# SemanticBountyEscrow

A standalone, reusable GenLayer Intelligent Contract primitive: an automated
milestone-based bounty escrow and deliverable verification engine for
decentralized work, technical bounties, and agent-to-agent task settlement.

It has no frontend by design. The contract owns the minimum state transitions
that need validator consensus: the escrow lifecycle, the deterministic keyword
gate, the rubric settlement decision, and the payout accounting.

- Runner: pinned `py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6`
- Network: `studionet` (chain id 61999)
- Deployed contract address: `0x0b15E693cbeDEBf921bd59FD95213F9293CE3bCf`
- Source sha256 (local, on-chain and GitHub remote, byte-for-byte identical):
  `2ff928759b24db84f4f7b98425be4c34e141d55a0c09e59f3ea33461879f226d`

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
| `SUBMITTED` | A deliverable awaits settlement | worker `withdraw_submission` -> `OPEN` (URL cleared); sponsor `approve_deterministic_settlement` or anyone `evaluate_deliverable_live` -> `SETTLED_PAID` or `REJECTED` |
| `REJECTED` | Scored below the bar, escrow still locked | worker `submit_deliverable` -> `SUBMITTED` (resubmission); sponsor `reclaim_bounty` -> `REFUNDED` |
| `SETTLED_PAID` | Terminal, worker credited | none |
| `REFUNDED` | Terminal, sponsor credited | none |

While a bounty is `SUBMITTED`, the sponsor is deliberately blocked from
`reclaim_bounty`, so it cannot grab the money out from under a pending review. If
the deliverable URL goes down or settlement stalls, the assigned worker (and only
the worker) can call `withdraw_submission` to return the bounty to `OPEN` and clear
the URL. That is the deadlock exit: after it the worker may resubmit a fixed
deliverable, and the sponsor may reclaim. `withdraw_submission` moves no funds.

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
- `withdraw_submission(bounty_id)` (worker only, `SUBMITTED` -> `OPEN`)
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

## Input hygiene bounds

Every free-text and numeric input is bounded to blunt storage bloat and
prompt-injection payloads. Violations revert with a specific message rather than
being clamped:

- `title`: 1..120 characters
- `requirements_rubric`: 1..2000 characters
- `required_keywords_csv`: at most 500 characters, at most 20 non-empty tokens
- `deliverable_url`: 8..512 characters, must start with `http://` or `https://`, no whitespace
- `deliverable_manifest`: 1..4000 characters
- `summary`: 1..500 characters
- `escrow_amount`: `0 < escrow_amount <= 10**30`

In `evaluate_deliverable_live`, the fetched page text is truncated to a
deterministic 8000-character window before it enters the prompt, and the stored
`audit_summary` is clipped to 500 characters on both settlement paths.

## Verification status

- `genvm-lint check`: 0 errors (the only message is an informational note that a
  newer runner exists; the pinned hash is kept deliberately as required).
- Direct-mode tests: `tests/test_bounty_escrow_direct.py`, 44 tests, all passing.
- Studionet integration test: `tests/test_bounty_escrow_integration.py`, passing
  against the live network (run with `STUDIONET_RUN=1 gltest ...`).

Studionet transaction hashes from the recorded run (all `FINALIZED`), contract
`0x0b15E693cbeDEBf921bd59FD95213F9293CE3bCf`:

| Step | Transaction |
|------|-------------|
| deploy | `0xba5c680b6cd2f78840da4ce6f4ba6760d184c8f0e908421d5f78b5c450783f86` |
| create_bounty | `0xdbfe18f349859481e7658b253987a4227540a0ffc761afae015c2f4887e09af6` |
| submit_deliverable | `0xc34d4dd7f851d99cc28200f3d02a7765707d15eae3aad93114204aa09dd3a259` |
| withdraw_submission (SUBMITTED -> OPEN) | `0x2a7d39a13f8155e1433fc11827e6a50d08a6dfba85f88fbc33e8f731224449f9` |
| submit_deliverable (resubmit) | `0x1d480da609c15614f47d1a4d32faec1571d9d715323c654443ed3a73f008b3d6` |
| approve_deterministic_settlement | `0xb7c6ac2ebba59ccc18213f126fc421638bcfa3e91250ea70254880c52ec6153d` |
| withdraw_payout | `0xee952c1ca2d2d02203f99f67e9bc2de64f919d411767b335c42dfb3fa3cd4ed9` |
| create_bounty (second) | `0x739977567c771ce70f1f37814a6e58b0a248dcd5354fac01ce62e0beb0a61cfe` |
| withdraw_submission (unauthorized, expected no-op) | `0x084664088dd2078e7b0da09995f038cda94f2c81323dbd9d730a7cb9856dd997` |

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
  (sponsor `reclaim_bounty`, worker `withdraw_submission`, or reject-then-reclaim),
  not on a timer. The `SUBMITTED` deadlock is closed by `withdraw_submission`.
- `evaluate_deliverable_live` reverts on unreachable evidence instead of
  auto-refunding; the worker can `withdraw_submission` to reopen and the sponsor
  can then reclaim, but the live path does not fail closed to refund by itself.
- Free-text and numeric inputs are hard-bounded at the contract level (see Input
  hygiene bounds). Deployers should still validate transport-level sizes.
- Direct-mode tests run only the leader path. They prove the state machine and
  guards, not validator agreement under a hostile page; that requires live
  studionet runs and is not fully covered for adversarial content here.

## Layout

```
contracts/semantic_bounty_escrow.py      the primitive
tests/test_bounty_escrow_direct.py       44 direct-mode tests
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
