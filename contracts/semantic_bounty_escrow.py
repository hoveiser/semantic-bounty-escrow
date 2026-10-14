# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

# SemanticBountyEscrow
#
# A standalone, reusable Intelligent Contract primitive that acts as an
# automated milestone-based bounty escrow and deliverable verification engine
# for decentralized work, technical bounties and agent-to-agent task
# settlement.
#
# Verification is two stage:
#   1. A deterministic keyword gate inside submit_deliverable. Obviously
#      incomplete work that omits mandatory technical tokens can never enter
#      the settlement path.
#   2. A rubric based evaluation driven by prompt_comparative so that
#      validators independently re-fetch the deliverable and agree on the score
#      tier and the pass/fail verdict before funds move.
#
# Escrow accounting is a strict conservation ledger: a bounty escrow_amount can
# be paid out (SETTLED_PAID) or refunded (REFUNDED) at most once, never both,
# because both are terminal statuses reachable only from non-terminal states.

from genlayer import *

import json
from dataclasses import dataclass


# Error classification prefixes so validators can compare failure paths.
ERROR_EXPECTED = "[EXPECTED]"


# Bounty lifecycle states.
STATUS_OPEN = "OPEN"
STATUS_SUBMITTED = "SUBMITTED"
STATUS_SETTLED_PAID = "SETTLED_PAID"
STATUS_REJECTED = "REJECTED"
STATUS_REFUNDED = "REFUNDED"

TERMINAL_STATUSES = (STATUS_SETTLED_PAID, STATUS_REFUNDED)


@allow_storage
@dataclass
class BountySpec:
    bounty_id: u256
    sponsor: Address
    worker: Address
    title: str
    requirements_rubric: str
    required_keywords_csv: str
    min_pass_score: u256
    escrow_amount: u256
    deliverable_url: str
    status: str
    achieved_score: u256
    payout_released: u256
    audit_summary: str


def _normalize_address(account: str) -> Address:
    """Coerce and validate a user supplied address string."""
    if account is None:
        raise gl.vm.UserError(f"{ERROR_EXPECTED} Missing address")
    text = str(account).strip()
    if len(text) == 0:
        raise gl.vm.UserError(f"{ERROR_EXPECTED} Empty address")
    try:
        return Address(text)
    except Exception:
        raise gl.vm.UserError(f"{ERROR_EXPECTED} Invalid address: {text}")


def _split_keywords(required_csv: str) -> list:
    """Return the list of mandatory tokens, dropping blanks."""
    tokens = []
    for raw in str(required_csv).split(","):
        token = raw.strip()
        if len(token) > 0:
            tokens.append(token)
    return tokens


def _missing_keywords(required_csv: str, manifest: str) -> list:
    """Deterministic Tier-1 check: tokens missing from the manifest text."""
    lowered = str(manifest).lower()
    missing = []
    for token in _split_keywords(required_csv):
        if token.lower() not in lowered:
            missing.append(token)
    return missing


def _clamp_score(raw) -> u256:
    """Coerce an LLM score into the closed range 0..100."""
    try:
        value = int(round(float(str(raw).strip())))
    except Exception:
        raise gl.vm.UserError(f"{ERROR_EXPECTED} Non numeric score from evaluator")
    if value < 0:
        value = 0
    if value > 100:
        value = 100
    return u256(value)


def _as_bool(raw) -> bool:
    """Interpret the keyword-presence flag from a JSON payload."""
    if isinstance(raw, bool):
        return raw
    text = str(raw).strip().lower()
    return text in ("true", "1", "yes")


def _parse_eval_json(text: str) -> dict:
    """Defensively parse an LLM JSON payload that may be wrapped in prose."""
    if isinstance(text, dict):
        return text
    content = str(text)
    first = content.find("{")
    last = content.rfind("}")
    if first == -1 or last == -1 or last <= first:
        raise gl.vm.UserError(f"{ERROR_EXPECTED} Evaluator returned no JSON object")
    snippet = content[first : last + 1]
    try:
        parsed = json.loads(snippet)
    except Exception:
        # Strip a single trailing comma that LLMs frequently emit.
        cleaned = snippet.replace(",}", "}").replace(",]", "]")
        parsed = json.loads(cleaned)
    if not isinstance(parsed, dict):
        raise gl.vm.UserError(f"{ERROR_EXPECTED} Evaluator JSON is not an object")
    return parsed


class SemanticBountyEscrow(gl.Contract):
    next_bounty_id: u256
    bounties: TreeMap[u256, BountySpec]
    claimable_payouts: TreeMap[Address, u256]
    sponsor_bounties: TreeMap[Address, DynArray[u256]]
    worker_bounties: TreeMap[Address, DynArray[u256]]

    def __init__(self):
        self.next_bounty_id = u256(1)

    @gl.public.write
    def create_bounty(
        self,
        worker: str,
        title: str,
        requirements_rubric: str,
        required_keywords_csv: str,
        min_pass_score: u256,
        escrow_amount: u256,
    ) -> u256:
        """Open a new escrowed bounty funded with an accounting escrow_amount."""
        sponsor = gl.message.sender_address
        worker_addr = _normalize_address(worker)

        if title is None or len(str(title).strip()) == 0:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} Title must not be empty")
        if requirements_rubric is None or len(str(requirements_rubric).strip()) == 0:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} Requirements rubric must not be empty")

        score_floor = u256(50)
        score_ceiling = u256(100)
        if min_pass_score < score_floor or min_pass_score > score_ceiling:
            raise gl.vm.UserError(
                f"{ERROR_EXPECTED} min_pass_score must be between 50 and 100"
            )
        if escrow_amount <= u256(0):
            raise gl.vm.UserError(f"{ERROR_EXPECTED} escrow_amount must be greater than 0")
        if worker_addr == sponsor:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} worker must differ from sponsor")

        bounty_id = u256(self.next_bounty_id)
        self.next_bounty_id = u256(self.next_bounty_id + u256(1))

        spec = BountySpec(
            bounty_id=bounty_id,
            sponsor=sponsor,
            worker=worker_addr,
            title=str(title),
            requirements_rubric=str(requirements_rubric),
            required_keywords_csv=str(required_keywords_csv),
            min_pass_score=u256(min_pass_score),
            escrow_amount=u256(escrow_amount),
            deliverable_url="",
            status=STATUS_OPEN,
            achieved_score=u256(0),
            payout_released=u256(0),
            audit_summary="",
        )
        self.bounties[bounty_id] = spec
        self.sponsor_bounties.get_or_insert_default(sponsor).append(bounty_id)
        self.worker_bounties.get_or_insert_default(worker_addr).append(bounty_id)
        return bounty_id

    @gl.public.write
    def submit_deliverable(
        self, bounty_id: u256, deliverable_url: str, deliverable_manifest: str
    ) -> None:
        """Worker submits a deliverable. Enforces the deterministic keyword gate."""
        sender = gl.message.sender_address
        if bounty_id not in self.bounties:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} Bounty does not exist")

        bounty = self.bounties[bounty_id]
        if sender != bounty.worker:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} Only the assigned worker may submit")
        if bounty.status != STATUS_OPEN and bounty.status != STATUS_REJECTED:
            raise gl.vm.UserError(
                f"{ERROR_EXPECTED} Bounty must be OPEN or REJECTED to submit"
            )

        url = str(deliverable_url)
        if not (url.startswith("http://") or url.startswith("https://")):
            raise gl.vm.UserError(
                f"{ERROR_EXPECTED} deliverable_url must start with http:// or https://"
            )
        manifest = str(deliverable_manifest)
        if len(manifest.strip()) == 0:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} deliverable_manifest must not be empty")

        missing = _missing_keywords(bounty.required_keywords_csv, manifest)
        if len(missing) > 0:
            raise gl.vm.UserError(
                f"{ERROR_EXPECTED} Missing mandatory keywords in manifest: "
                + ", ".join(missing)
            )

        bounty.deliverable_url = url
        bounty.status = STATUS_SUBMITTED
        self.bounties[bounty_id] = bounty

    @gl.public.write
    def approve_deterministic_settlement(
        self, bounty_id: u256, verified_score: u256, summary: str
    ) -> str:
        """Sponsor settles a submitted bounty using a trusted external score."""
        sender = gl.message.sender_address
        if bounty_id not in self.bounties:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} Bounty does not exist")

        bounty = self.bounties[bounty_id]
        if sender != bounty.sponsor:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} Only the sponsor may settle deterministically")
        if bounty.status != STATUS_SUBMITTED:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} Bounty must be SUBMITTED to settle")
        if verified_score < u256(0) or verified_score > u256(100):
            raise gl.vm.UserError(f"{ERROR_EXPECTED} verified_score must be between 0 and 100")

        audit = str(summary)
        if verified_score >= bounty.min_pass_score:
            bounty.status = STATUS_SETTLED_PAID
            bounty.achieved_score = u256(verified_score)
            bounty.payout_released = bounty.escrow_amount
            bounty.audit_summary = audit
            self.bounties[bounty_id] = bounty
            current = self.claimable_payouts.get_or_insert_default(bounty.worker)
            self.claimable_payouts[bounty.worker] = u256(current + bounty.escrow_amount)
            return STATUS_SETTLED_PAID
        else:
            bounty.status = STATUS_REJECTED
            bounty.achieved_score = u256(verified_score)
            bounty.payout_released = u256(0)
            bounty.audit_summary = audit
            self.bounties[bounty_id] = bounty
            return STATUS_REJECTED

    @gl.public.write
    def evaluate_deliverable_live(self, bounty_id: u256) -> str:
        """Consensus rubric evaluation of a submitted deliverable via live fetch."""
        if bounty_id not in self.bounties:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} Bounty does not exist")

        bounty = self.bounties[bounty_id]
        if bounty.status != STATUS_SUBMITTED:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} Bounty must be SUBMITTED to evaluate live")

        # Snapshot every field into plain Python values BEFORE the non-det block.
        # No storage access happens inside the non-deterministic function.
        bounty_id_local = u256(bounty.bounty_id)
        worker_local = bounty.worker
        sponsor_local = bounty.sponsor
        title_local = str(bounty.title)
        rubric_local = str(bounty.requirements_rubric)
        keywords_local = str(bounty.required_keywords_csv)
        min_pass_local = u256(bounty.min_pass_score)
        url_local = str(bounty.deliverable_url)
        escrow_local = u256(bounty.escrow_amount)

        def run_evaluation() -> dict:
            page = gl.nondet.web.render(url_local, mode="text")
            page_text = str(page)
            prompt = (
                "You are a strict technical deliverable evaluator. "
                "Evaluate the deliverable against the rubric.\n"
                f"TITLE: {title_local}\n"
                f"RUBRIC: {rubric_local}\n"
                f"MANDATORY_KEYWORDS_CSV: {keywords_local}\n"
                f"MIN_PASS_SCORE: {int(min_pass_local)}\n"
                f"DELIVERABLE_TEXT:\n{page_text}\n"
                "Score the deliverable 0 to 100 for rubric compliance. "
                "Set all_mandatory_keywords_present to true only if every "
                "mandatory keyword appears in the deliverable text. "
                "verdict must be PASS only if all keywords are present and "
                "score is at least MIN_PASS_SCORE, otherwise FAIL. "
                "Return canonical JSON exactly shaped as: "
                '{"score": <0..100>, '
                '"all_mandatory_keywords_present": <true|false>, '
                '"verdict": "PASS" or "FAIL", '
                '"summary": "<concise evaluation>"}'
            )
            raw = gl.nondet.exec_prompt(prompt, response_format="json")
            parsed = _parse_eval_json(raw)

            if "score" not in parsed:
                raise gl.vm.UserError(f"{ERROR_EXPECTED} Evaluator JSON missing score")

            score_local = _clamp_score(parsed.get("score"))
            present = _as_bool(parsed.get("all_mandatory_keywords_present"))
            summary_local = str(parsed.get("summary", ""))

            # Re-run the deterministic keyword gate against the fetched text so a
            # hallucinated presence flag cannot by itself unlock a payout.
            gate_missing = _missing_keywords(keywords_local, page_text)
            if len(gate_missing) > 0:
                present = False

            if present and score_local >= min_pass_local:
                verdict_local = "PASS"
            else:
                verdict_local = "FAIL"

            return {
                "bounty_id": int(bounty_id_local),
                "score": int(score_local),
                "all_mandatory_keywords_present": present,
                "verdict": verdict_local,
                "summary": summary_local,
            }

        def validate(result: dict) -> dict:
            # Normalise the leader payload through the same deterministic gates so
            # a malformed or hallucinated verdict cannot diverge from validator
            # re-derivation. The comparative principle re-runs run_evaluation and
            # checks the score tier plus pass/fail verdict agree.
            if not isinstance(result, dict):
                raise gl.vm.UserError(f"{ERROR_EXPECTED} Evaluation result is not an object")
            if result.get("verdict") not in ("PASS", "FAIL"):
                raise gl.vm.UserError(f"{ERROR_EXPECTED} Evaluation verdict is not PASS or FAIL")
            if result.get("score") is None:
                raise gl.vm.UserError(f"{ERROR_EXPECTED} Evaluation score missing")
            return result

        evaluated = gl.eq_principle.prompt_comparative(
            run_evaluation,
            principle=(
                "The verdict field must be exactly the same (PASS or FAIL). "
                "The score must fall in the same band relative to MIN_PASS_SCORE "
                "(below / at-or-above). all_mandatory_keywords_present must match. "
                "Summary wording may differ."
            ),
        )
        result = validate(evaluated)

        # Deterministic settlement accounting on the agreed outcome.
        score_final = _clamp_score(result.get("score"))
        present_final = _as_bool(result.get("all_mandatory_keywords_present"))
        summary_final = str(result.get("summary", ""))

        bounty = self.bounties[bounty_id_local]
        # Lock against double payout: only a still SUBMITTED bounty settles here.
        if bounty.status != STATUS_SUBMITTED:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} Bounty is no longer SUBMITTED")

        if present_final and score_final >= min_pass_local:
            bounty.status = STATUS_SETTLED_PAID
            bounty.achieved_score = score_final
            bounty.payout_released = escrow_local
            bounty.audit_summary = summary_final
            self.bounties[bounty_id_local] = bounty
            current = self.claimable_payouts.get_or_insert_default(worker_local)
            self.claimable_payouts[worker_local] = u256(current + escrow_local)
            return STATUS_SETTLED_PAID
        else:
            bounty.status = STATUS_REJECTED
            bounty.achieved_score = score_final
            bounty.payout_released = u256(0)
            bounty.audit_summary = summary_final
            self.bounties[bounty_id_local] = bounty
            return STATUS_REJECTED

    @gl.public.write
    def reclaim_bounty(self, bounty_id: u256) -> u256:
        """Sponsor refunds an unsubmitted or rejected bounty."""
        sender = gl.message.sender_address
        if bounty_id not in self.bounties:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} Bounty does not exist")

        bounty = self.bounties[bounty_id]
        if sender != bounty.sponsor:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} Only the sponsor may reclaim")
        if bounty.status != STATUS_OPEN and bounty.status != STATUS_REJECTED:
            raise gl.vm.UserError(
                f"{ERROR_EXPECTED} Only OPEN or REJECTED bounties may be reclaimed"
            )

        reclaimed = bounty.escrow_amount
        bounty.status = STATUS_REFUNDED
        bounty.payout_released = u256(0)
        self.bounties[bounty_id] = bounty
        current = self.claimable_payouts.get_or_insert_default(bounty.sponsor)
        self.claimable_payouts[bounty.sponsor] = u256(current + reclaimed)
        return u256(reclaimed)

    @gl.public.write
    def withdraw_payout(self) -> u256:
        """Caller withdraws their accrued claimable payout ledger balance."""
        sender = gl.message.sender_address
        balance = self.claimable_payouts.get_or_insert_default(sender)
        if balance <= u256(0):
            raise gl.vm.UserError(f"{ERROR_EXPECTED} No claimable payout to withdraw")
        amount = u256(balance)
        self.claimable_payouts[sender] = u256(0)
        return amount

    @gl.public.view
    def get_bounty(self, bounty_id: u256) -> dict:
        if bounty_id not in self.bounties:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} Bounty does not exist")
        bounty = self.bounties[bounty_id]
        return {
            "bounty_id": int(bounty.bounty_id),
            "sponsor": bounty.sponsor.as_hex,
            "worker": bounty.worker.as_hex,
            "title": bounty.title,
            "requirements_rubric": bounty.requirements_rubric,
            "required_keywords_csv": bounty.required_keywords_csv,
            "min_pass_score": int(bounty.min_pass_score),
            "escrow_amount": int(bounty.escrow_amount),
            "deliverable_url": bounty.deliverable_url,
            "status": bounty.status,
            "achieved_score": int(bounty.achieved_score),
            "payout_released": int(bounty.payout_released),
            "audit_summary": bounty.audit_summary,
        }

    @gl.public.view
    def get_claimable_payout(self, account: str) -> u256:
        addr = _normalize_address(account)
        return u256(self.claimable_payouts.get(addr, u256(0)))

    @gl.public.view
    def get_escrow_summary(self) -> dict:
        total = 0
        open_count = 0
        submitted_count = 0
        settled_count = 0
        rejected_count = 0
        refunded_count = 0
        total_locked = 0
        total_released = 0
        for key in self.bounties.keys():
            bounty = self.bounties[key]
            total += 1
            status = bounty.status
            if status == STATUS_OPEN:
                open_count += 1
                total_locked += int(bounty.escrow_amount)
            elif status == STATUS_SUBMITTED:
                submitted_count += 1
                total_locked += int(bounty.escrow_amount)
            elif status == STATUS_SETTLED_PAID:
                settled_count += 1
                total_released += int(bounty.payout_released)
            elif status == STATUS_REJECTED:
                rejected_count += 1
                total_locked += int(bounty.escrow_amount)
            elif status == STATUS_REFUNDED:
                refunded_count += 1
        return {
            "total_bounties": total,
            "open": open_count,
            "submitted": submitted_count,
            "settled_paid": settled_count,
            "rejected": rejected_count,
            "refunded": refunded_count,
            "total_escrow_locked": total_locked,
            "total_payout_released": total_released,
        }

    @gl.public.view
    def get_actor_bounties(self, account: str) -> dict:
        addr = _normalize_address(account)
        sponsor_ids = []
        worker_ids = []
        if addr in self.sponsor_bounties:
            arr = self.sponsor_bounties[addr]
            for i in range(len(arr)):
                sponsor_ids.append(int(arr[i]))
        if addr in self.worker_bounties:
            arr = self.worker_bounties[addr]
            for i in range(len(arr)):
                worker_ids.append(int(arr[i]))
        return {"sponsor": sponsor_ids, "worker": worker_ids}
