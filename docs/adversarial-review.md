# Adversarial Review: SemanticBountyEscrow

This note attacks the contract as if every counterparty (sponsor, worker, any
caller of the permissionless evaluate path, validators, and the content of any
fetched deliverable) is adversarial. Each finding is marked FIXED with the
proof, or OPEN with the reason. Nothing here is a success-only summary.

Scope of this review: `contracts/semantic_bounty_escrow.py`. Proof tests:
`tests/test_bounty_escrow_direct.py` (27 direct-mode tests, all passing).

## 1. State machine table

| Status | Meaning | Who may leave it | Method | Next status | Fund effect |
|--------|---------|------------------|--------|-------------|-------------|
| OPEN | Escrowed, no deliverable | worker | submit_deliverable | SUBMITTED | none (locked) |
| OPEN | Escrowed, no deliverable | sponsor | reclaim_bounty | REFUNDED | credit sponsor escrow |
| REJECTED | Deliverable scored below bar | worker | submit_deliverable | SUBMITTED | none (still locked) |
| REJECTED | Deliverable scored below bar | sponsor | reclaim_bounty | REFUNDED | credit sponsor escrow |
| SUBMITTED | Awaiting settlement | sponsor | approve_deterministic_settlement | SETTLED_PAID or REJECTED | credit worker escrow on pass, none on reject |
| SUBMITTED | Awaiting settlement | anyone | evaluate_deliverable_live | SETTLED_PAID or REJECTED | credit worker escrow on pass, none on reject |
| SETTLED_PAID | Terminal, worker paid | none | none | none | already credited |
| REFUNDED | Terminal, sponsor refunded | none | none | none | already credited |

Reclaim and approve are blocked while a bounty is SUBMITTED, so the sponsor
cannot rug a worker who is mid-review.

## 2. Liveness: no stuck funds

- OPEN: recoverable by the sponsor at any time through `reclaim_bounty`.
- REJECTED: recoverable by the sponsor through `reclaim_bounty`; re-submittable
  by the worker.
- SUBMITTED: recoverable by the sponsor. The sponsor is deliberately blocked
  from direct `reclaim_bounty` here (so it cannot abandon a pending review and
  grab the money), but it always retains `approve_deterministic_settlement`,
  which can move the bounty to REJECTED and then to REFUNDED. The worker is
  never able to pull the sponsor's escrow while it is SUBMITTED, which is the
  correct direction: it is the sponsor's money until earned.
- No on-chain wall-clock deadline exists. This is an accepted design tradeoff:
  both parties always hold a unilateral exit that is guarded by role and status,
  so funds are not permanently locked. There is no time window, so the project
  rule about a floor and ceiling on every time window is not applicable.

Verdict: FIXED. Every terminal fund movement is reachable only through a
role-gated, status-gated method, and no status is a dead end for the owner of
the funds.

## 3. Race conditions between competing transitions

Competing pairs on one bounty: submit vs reclaim, approve vs evaluate, and a
second settle after the first.

- All credit-bearing and state-changing methods re-read `status` and require a
  specific non-terminal value. Once any transition commits, every competing
  method fails its status guard and reverts. GenLayer serializes and finalizes
  transactions, so only one competing transition can win per bounty.
- `evaluate_deliverable_live` re-checks `bounty.status == SUBMITTED` again
  immediately before crediting, as defense in depth between the non-deterministic
  block and the accounting write.
- Test `test_double_settlement_blocked` proves a second approve reverts and the
  worker ledger is not credited twice (stays at exactly one escrow).

Verdict: FIXED. Guards are mutually exclusive by status. Because there is no
deadline, there is no timestamp race to split.

## 4. Accounting invariant (escrow conservation)

Invariant, one line: each bounty `escrow_amount` is credited at most once, to
either the worker (SETTLED_PAID) or the sponsor (REFUNDED), never to both, and
never twice to the same party.

- The only three paths that touch `claimable_payouts` with a bounty amount are
  approve-pass (worker), evaluate-pass (worker), and reclaim (sponsor).
- All three move the bounty to a terminal state or, in the reject case, to a
  state that can only reach terminal through one of those same single-credit
  paths. SETTLED_PAID and REFUNDED are terminal and reject all further credits.
- `withdraw_payout` reads the running balance, zeroes it, and reverts on zero,
  so a credit can be withdrawn at most once.
- `get_escrow_summary` exposes the invariant terms (total locked across OPEN,
  SUBMITTED and REJECTED; total released across SETTLED_PAID).

Verdict: FIXED for double-payout, double-withdraw and pay-plus-refund. Test
coverage: deterministic pass/reject, live pass/low-score/missing-keyword,
reclaim on OPEN and REJECTED, blocked reclaim on SUBMITTED and SETTLED_PAID,
double-settlement block, withdraw then re-withdraw revert.

### Ledger-only caveat (honest limitation, by design)

The contract does not receive or custody native GLX. `create_bounty` is not
payable and never calls `gl.value`, and `withdraw_payout` does not transfer
native value. `escrow_amount`, `claimable_payouts` and `payout_released` are an
internal accounting ledger. The conservation invariant above is therefore
proven over the ledger, not over a balance held by the contract. An integrator
must lock and move the real GLX outside this primitive. This is exactly what the
task specification asked for, but it is a security-relevant limitation, not a
self-custodial vault.

Verdict: OPEN as a design limitation, documented, not a code defect.

## 5. Input validation at entry points

- create_bounty: title and rubric must be non-empty; `min_pass_score` bounded to
  the closed range 50 to 100 (floor and ceiling); `escrow_amount` must be
  positive; `worker` must parse to a valid `Address`; `worker` must differ from
  the `sponsor`. Tested.
- submit_deliverable: sender must equal the assigned worker; status must be OPEN
  or REJECTED; url must start with http:// or https://; manifest must be
  non-empty; the deterministic keyword gate rejects any missing mandatory token.
  Tested.
- approve_deterministic_settlement: sender must equal the sponsor; status must
  be SUBMITTED; `verified_score` bounded 0 to 100. Tested.
- evaluate_deliverable_live: status must be SUBMITTED; score clamped to 0 to 100;
  keywords re-checked against the actually fetched text. Tested.
- withdraw_payout: reverts on a zero balance. Tested.

Verdict: FIXED for the fields above.

OPEN: There are no upper length caps on `title`, `requirements_rubric`,
`required_keywords_csv`, `deliverable_url`, `deliverable_manifest` or `summary`.
The task specification did not request caps, so none were added, but very large
strings increase storage and gas and can bloat the prompt. A production deployer
should impose byte limits at the boundary. Flagged honestly as unmitigated.

## 6. Consensus safety (GenLayer)

- No free-form number from an LLM drives a transfer. The payout amount is fixed
  at `escrow_amount` during `create_bounty`. The LLM only influences the
  pass/fail decision, and only through a clamped score band and a boolean, both
  of which validators compare via `gl.eq_principle.prompt_comparative` (they
  independently re-render the url and re-run the prompt, then must agree on the
  verdict and the below/at-or-above `min_pass_score` band).
- Storage is never read inside the non-deterministic block. Every field the
  evaluator needs is snapshotted into plain Python locals first.
- External evidence is re-fetched independently by each validator rather than
  trusting a self-reported claim, because the live path renders `deliverable_url`
  itself instead of using the submitted manifest.
- Prompt injection handling: the deliverable text is untrusted input. The
  contract does not trust the LLM's `all_mandatory_keywords_present` flag. It
  recomputes keyword presence deterministically from the raw fetched text and
  forces the flag to false when a mandatory token is literally absent, so an
  injected instruction inside the page cannot fabricate a keyword that is not
  there. This specific override is exercised by
  `test_live_evaluation_missing_keyword_overrides_hallucinated_pass`.
- Fail-closed behavior: currently a failed fetch or an unparseable LLM response
  raises inside the transaction, which reverts with no state change. It does not
  automatically refund. The sponsor still has the manual reject-then-reclaim
  exit, so funds are recoverable, but the live path does not itself fall back to
  refund on unreachable evidence.

Verdict: PARTIALLY FIXED. Amount decoupled from LLM, no storage in nondet, and
deterministic keyword override are done and tested. Fail-closed-to-refund on a
dead URL is OPEN (manual sponsor exit exists). Consensus agreement under a
hostile page is only partly covered: direct mode runs the leader only and does
NOT prove validator agreement, so validator behavior must be confirmed on a live
studionet run before this is called injection-proof end to end. It is not called
injection-proof here.

## 7. Test coverage summary

Negative guard tests: self-dealing sponsor equals worker; low and out-of-range
scores; zero escrow; empty title; invalid address; unauthorized submitter;
missing-keyword reject; bad url; empty manifest; sponsor-only settle; blocked
reclaim on SUBMITTED and SETTLED_PAID; withdraw on empty ledger. Scenario and
invariant tests: two settlement paths, live evaluation pass/low-score/
missing-keyword, resubmission after reject, double-settlement block, summary and
actor indexes, withdraw. All pass under gltest direct mode.

## Findings list

- FIXED: state machine has no dead-end for the fund owner (role + status
  guarded exits).
- FIXED: no double credit, no double withdraw, no pay-plus-refund, second settle
  blocked. Proven by direct tests.
- FIXED: transfer amount is not an LLM number; storage is not read inside the
  non-deterministic block; keyword presence recomputed from fetched text.
- OPEN: contract is a ledger, not a GLX vault; integrator must custody funds.
  By spec, documented.
- OPEN: no upper length caps on free-text fields. By spec omission, flagged.
- OPEN: evaluate path does not auto-refund on unreachable evidence; manual
  sponsor exit only.
- OPEN: validator agreement under a hostile page is not proven by direct-mode
  leader-only tests; requires live studionet coverage before claiming full
  injection resistance.
