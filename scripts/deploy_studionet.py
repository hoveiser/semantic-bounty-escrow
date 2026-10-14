# Studionet deployment driver and on-chain verifier for SemanticBountyEscrow.

"""Deploy SemanticBountyEscrow to GenLayer studionet, drive a real multi-account
escrow scenario (create, submit, deterministic settlement, an unauthorized
rollback attempt), verify every transaction reached FINALIZED with the expected
on-chain state, prove byte-for-byte sha256 identity between the local source and
the deployed code returned by gen_getContractCode, and write the evidence to
evidence/studionet_deployment.json.

Private key and token are read only from this project's own .env. They are never
printed.

Run:  python scripts/deploy_studionet.py
"""

import hashlib
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = REPO_ROOT / "contracts" / "semantic_bounty_escrow.py"
EVIDENCE_PATH = REPO_ROOT / "evidence" / "studionet_deployment.json"

sys.path.insert(0, str(REPO_ROOT))

from genlayer_py import create_account, create_client  # noqa: E402
from genlayer_py.chains import studionet  # noqa: E402
from genlayer_py.types import TransactionStatus  # noqa: E402


WAIT_INTERVAL_MS = 3000
WAIT_RETRIES = 80

TITLE = "Implement settlement module"
RUBRIC = "Clear module, escrow accounting, documented methods"
KEYWORDS = "settlement,escrow,test"
MANIFEST = (
    "Delivers the settlement engine with escrow accounting and a full test "
    "suite for the module."
)
DELIVERABLE_URL = "https://example.com/deliverable/settlement-module"
ESCROW_AMOUNT = 1_000_000
MIN_PASS = 75
PASS_SCORE = 90


def load_env():
    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        raise SystemExit(
            "Missing .env in the project root. Add GENLAYER_PRIVATE_KEY and "
            "GITHUB_TOKEN there, then re-run. Never commit .env."
        )
    values = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        values[key.strip()] = val.strip()
    private_key = values.get("GENLAYER_PRIVATE_KEY")
    if not private_key:
        raise SystemExit("GENLAYER_PRIVATE_KEY is empty or missing in .env")
    return private_key


def to_int(value):
    """Coerce a chained integer id that may arrive as int or JSON-quoted str."""
    if isinstance(value, bool):
        raise ValueError("unexpected bool where int expected")
    if isinstance(value, int):
        return value
    text = str(value).strip().strip('"').strip("'")
    return int(text)


def unwrap(value):
    """Strip a single layer of JSON-style quoting from a returned string."""
    if isinstance(value, str):
        text = value.strip()
        if len(text) >= 2 and text[0] == text[-1] and text[0] in ('"', "'"):
            return text[1:-1]
    return value


def extract_address(receipt):
    for container in ("tx_data_decoded", "data"):
        block = receipt.get(container)
        if isinstance(block, dict) and "contract_address" in block:
            return block["contract_address"]
    raise ValueError("deployment receipt missing contract_address")


def drive_write(client, address, method, args, account):
    tx_hash = client.write_contract(address, method, args=args, account=account)
    receipt = client.wait_for_transaction_receipt(
        tx_hash,
        status=TransactionStatus.FINALIZED,
        interval=WAIT_INTERVAL_MS,
        retries=WAIT_RETRIES,
    )
    status_name = receipt.get("status_name") or receipt.get("status")
    return {
        "method": method,
        "tx_hash": tx_hash,
        "status": str(status_name),
        "reached_finalized": _is_finalized(receipt),
    }


def _is_finalized(receipt):
    # wait_for_transaction_receipt only returns once the requested FINALIZED
    # status is observed, so a receipt in hand means FINALIZED. Confirm through
    # the human-readable name when present.
    name = receipt.get("status_name")
    if isinstance(name, str):
        return name.upper() == "FINALIZED"
    return True


def read_view(client, address, method, args, account):
    return client.read_contract(address, method, args=args, account=account)


def fetch_contract_code(client, address, attempts=6, pause=6.0):
    last_error = None
    for _ in range(attempts):
        try:
            response = client.provider.make_request("gen_getContractCode", [address])
            if "error" in response:
                raise RuntimeError(f"gen_getContractCode error: {response['error']}")
            result = response.get("result")
            if result is None:
                raise RuntimeError("gen_getContractCode returned null result")
            if isinstance(result, (bytes, bytearray)):
                return bytes(result)
            text = result
            if isinstance(text, str) and text.startswith("0x"):
                try:
                    return bytes.fromhex(text[2:])
                except ValueError:
                    return text.encode("utf-8")
            # studionet returns the stored source base64-encoded.
            import base64

            try:
                return base64.b64decode(text, validate=True)
            except Exception:  # noqa: BLE001
                return str(text).encode("utf-8")
        except Exception as exc:  # noqa: BLE001
            # Transient gateway errors (502/503) return HTML, not JSON. Retry.
            last_error = exc
            import time

            time.sleep(pause)
    raise RuntimeError(f"gen_getContractCode failed after retries: {last_error}")


def probe_explorer(deploy_tx):
    """Best-effort hosted-explorer check. Recorded honestly either way."""
    from urllib.request import urlopen, Request

    base = "https://genlayer-explorer.vercel.app"
    url = f"{base}/api/transactions/{deploy_tx}"
    try:
        req = Request(url, headers={"User-Agent": "semantic-bounty-escrow"})
        with urlopen(req, timeout=20) as resp:
            return {"url": url, "ok": True, "http_status": resp.status}
    except Exception as exc:  # noqa: BLE001
        detail = str(exc)
        if hasattr(exc, "code"):
            detail = f"HTTP {exc.code}"
        return {"url": url, "ok": False, "error": detail}


def run_scenario():
    private_key = load_env()
    sponsor = create_account(private_key)
    worker = create_account()
    charlie = create_account()

    client = create_client(chain=studionet, account=sponsor)

    source_bytes = CONTRACT_PATH.read_bytes()
    code = source_bytes.decode("utf-8")
    local_sha256 = hashlib.sha256(code.encode("utf-8")).hexdigest()

    transactions = []

    deploy_tx = client.deploy_contract(code=code, args=[])
    deploy_receipt = client.wait_for_transaction_receipt(
        deploy_tx,
        status=TransactionStatus.FINALIZED,
        interval=WAIT_INTERVAL_MS,
        retries=WAIT_RETRIES,
    )
    address = extract_address(deploy_receipt)
    transactions.append(
        {
            "method": "deploy",
            "tx_hash": deploy_tx,
            "status": str(
                deploy_receipt.get("status_name") or deploy_receipt.get("status")
            ),
            "reached_finalized": _is_finalized(deploy_receipt),
        }
    )

    # 1. Sponsor opens a bounty for the worker.
    transactions.append(
        drive_write(
            client,
            address,
            "create_bounty",
            [worker.address, TITLE, RUBRIC, KEYWORDS, MIN_PASS, ESCROW_AMOUNT],
            sponsor,
        )
    )
    sponsor_view = read_view(
        client, address, "get_actor_bounties", [sponsor.address], sponsor
    )
    bounty_id = to_int(sponsor_view["sponsor"][-1])

    # 2. Worker submits a deliverable that clears the deterministic keyword gate.
    transactions.append(
        drive_write(
            client,
            address,
            "submit_deliverable",
            [bounty_id, DELIVERABLE_URL, MANIFEST],
            worker,
        )
    )

    # 3. Sponsor settles deterministically with a passing score.
    settle_tx = drive_write(
        client,
        address,
        "approve_deterministic_settlement",
        [bounty_id, PASS_SCORE, "Meets rubric and keyword bar"],
        sponsor,
    )
    transactions.append(settle_tx)

    # 4. Expected rollback: an unauthorized account tries to submit on a fresh
    #    bounty. The transaction finalizes but must leave that bounty OPEN.
    transactions.append(
        drive_write(
            client,
            address,
            "create_bounty",
            [worker.address, "Second bounty", RUBRIC, KEYWORDS, MIN_PASS, ESCROW_AMOUNT],
            sponsor,
        )
    )
    second_view = read_view(
        client, address, "get_actor_bounties", [sponsor.address], sponsor
    )
    second_id = to_int(second_view["sponsor"][-1])
    unauthorized_tx = drive_write(
        client,
        address,
        "submit_deliverable",
        [second_id, DELIVERABLE_URL, MANIFEST],
        charlie,
    )
    unauthorized_tx["expected"] = "no state change (wrong caller)"
    transactions.append(unauthorized_tx)
    second_bounty_after = read_view(client, address, "get_bounty", [second_id], sponsor)

    # Final state reads for the settled bounty.
    settled = read_view(client, address, "get_bounty", [bounty_id], sponsor)
    worker_claim = to_int(
        read_view(client, address, "get_claimable_payout", [worker.address], sponsor)
    )
    summary = read_view(client, address, "get_escrow_summary", [], sponsor)

    chain_code = fetch_contract_code(client, address)
    chain_sha256 = hashlib.sha256(chain_code).hexdigest()
    sha256_match = chain_sha256 == local_sha256

    explorer = probe_explorer(deploy_tx)

    # Assertions. Anything weaker raises before evidence is finalized.
    assert str(settled["status"]) == "SETTLED_PAID", settled
    assert to_int(settled["achieved_score"]) == PASS_SCORE, settled
    assert to_int(settled["payout_released"]) == ESCROW_AMOUNT, settled
    assert worker_claim == ESCROW_AMOUNT, worker_claim
    assert str(unwrap(second_bounty_after["status"])) == "OPEN", second_bounty_after
    assert all(t["reached_finalized"] for t in transactions), transactions

    result = {
        "network": "studionet",
        "rpc_url": studionet.rpc_urls["default"]["http"][0],
        "explorer_url": "https://genlayer-explorer.vercel.app",
        "chain_id": studionet.id,
        "sponsor_address": sponsor.address,
        "worker_address": worker.address,
        "contract_address": address,
        "deploy_tx": deploy_tx,
        "local_sha256": local_sha256,
        "chain_sha256": chain_sha256,
        "sha256_identity": sha256_match,
        "explorer_probe": explorer,
        "final_state": {
            "settled_bounty": settled,
            "worker_claimable": worker_claim,
            "escrow_summary": summary,
            "unauthorized_attempt_bounty_status": unwrap(
                second_bounty_after["status"]
            ),
        },
        "transactions": transactions,
    }
    return result


def main():
    result = run_scenario()
    EVIDENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE_PATH.write_text(
        json.dumps(result, indent=2, default=str), encoding="utf-8"
    )

    # Console summary never prints secrets.
    print("network:", result["network"])
    print("contract_address:", result["contract_address"])
    print("deploy_tx:", result["deploy_tx"])
    print("transactions:", len(result["transactions"]))
    for tx in result["transactions"]:
        print(
            f"  {tx['method']:<32} {tx['status']:<10} "
            f"finalized={tx['reached_finalized']} {tx['tx_hash']}"
        )
    print("local_sha256:", result["local_sha256"])
    print("chain_sha256:", result["chain_sha256"])
    print("sha256_identity:", result["sha256_identity"])
    print("explorer_probe:", result["explorer_probe"])
    print("worker_claimable:", result["final_state"]["worker_claimable"])
    print("evidence_written:", str(EVIDENCE_PATH))
    if not result["sha256_identity"]:
        raise SystemExit("Deployed code does not match local source sha256")


if __name__ == "__main__":
    main()
