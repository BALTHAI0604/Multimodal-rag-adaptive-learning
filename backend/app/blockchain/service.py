"""Blockchain integrity service (web3.py + the StudentAcademicAudit contract).

What goes on-chain: an opaque random record id and an HMAC-SHA256 of the canonical attempt record.
What stays in the database: all marks, names and answers.  A record is only reported as "anchored"
after a mined transaction receipt with status 1; if the node is down the record stays "pending"
and can be retried - the platform never claims protection that does not exist.
"""
from __future__ import annotations

import json
import logging
import secrets
from pathlib import Path
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.config import settings
from app.models.models import (
    AssessmentAttempt, BlockchainRecord, BlockchainVerification, StudentAnswer, utcnow,
)
from app.security.crypto import keyed_hash, canonical_json

log = logging.getLogger("blockchain")

ABI_PATH = Path(__file__).resolve().parents[3] / "blockchain" / "abi" / "StudentAcademicAudit.json"
_w3 = None
_contract = None
_account = None
_provider_override = None            # tests supply an EthereumTesterProvider
_contract_address_override: Optional[str] = None


def load_artifact() -> Dict[str, Any]:
    return json.loads(ABI_PATH.read_text())


def set_test_chain(w3, contract, account) -> None:
    global _w3, _contract, _account
    _w3, _contract, _account = w3, contract, account


def reset() -> None:
    global _w3, _contract, _account
    _w3 = _contract = _account = None


def _connect():
    """Returns (w3, contract, account) or raises RuntimeError with a readable reason."""
    global _w3, _contract, _account
    if _contract is not None and _w3 is not None:
        # A cached handle can go stale (node stopped, local chain reset/redeployed). Re-validate cheaply:
        # sending a transaction to an address with no code "succeeds" on EVM, which must never count as anchoring.
        try:
            if _w3.eth.get_code(_contract.address):
                return _w3, _contract, _account
            stale = "No contract is deployed at CONTRACT_ADDRESS on this chain (was the node restarted/reset?)."
        except Exception as exc:
            stale = f"Cannot reach the blockchain node ({str(exc)[:120]})."
        reset()
        raise RuntimeError(stale)
    if not settings.BLOCKCHAIN_ENABLED:
        raise RuntimeError("Blockchain anchoring is disabled (BLOCKCHAIN_ENABLED=false).")
    if not settings.CONTRACT_ADDRESS:
        raise RuntimeError("CONTRACT_ADDRESS is not set. Deploy the contract (python -m app.cli deploy-contract).")
    from web3 import Web3
    w3 = Web3(Web3.HTTPProvider(settings.BLOCKCHAIN_RPC_URL, request_kwargs={"timeout": 8}))
    if not w3.is_connected():
        raise RuntimeError(f"Cannot reach the blockchain node at {settings.BLOCKCHAIN_RPC_URL}.")
    art = load_artifact()
    contract = w3.eth.contract(address=Web3.to_checksum_address(settings.CONTRACT_ADDRESS), abi=art["abi"])
    if not w3.eth.get_code(contract.address):
        raise RuntimeError("No contract is deployed at CONTRACT_ADDRESS on this chain (was the node restarted?).")
    account = None
    if settings.BLOCKCHAIN_PRIVATE_KEY:
        account = w3.eth.account.from_key(settings.BLOCKCHAIN_PRIVATE_KEY)
    _w3, _contract, _account = w3, contract, account
    return _w3, _contract, _account


def deploy_contract(rpc_url: str, private_key: str = "") -> Dict[str, Any]:
    from web3 import Web3
    w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 15}))
    if not w3.is_connected():
        raise RuntimeError(f"Cannot reach the blockchain node at {rpc_url}. Start it with: npx hardhat node")
    art = load_artifact()
    factory = w3.eth.contract(abi=art["abi"], bytecode=art["bytecode"])
    if private_key:
        acct = w3.eth.account.from_key(private_key)
        tx = factory.constructor().build_transaction({"from": acct.address, "nonce": w3.eth.get_transaction_count(acct.address),
                                                      "chainId": w3.eth.chain_id})
        h = w3.eth.send_raw_transaction(acct.sign_transaction(tx).raw_transaction)
    else:
        h = factory.constructor().transact({"from": w3.eth.accounts[0]})
    r = w3.eth.wait_for_transaction_receipt(h, timeout=60)
    return {"address": r.contractAddress, "chain_id": w3.eth.chain_id}


def _send(fn) -> Any:
    w3, contract, acct = _connect()
    if acct is not None:
        tx = fn.build_transaction({"from": acct.address, "nonce": w3.eth.get_transaction_count(acct.address),
                                   "chainId": w3.eth.chain_id})
        h = w3.eth.send_raw_transaction(acct.sign_transaction(tx).raw_transaction)
    else:
        sender = _account_address_fallback(w3)
        h = fn.transact({"from": sender})
    receipt = w3.eth.wait_for_transaction_receipt(h, timeout=60)
    if receipt.status != 1:
        raise RuntimeError("Transaction reverted on-chain.")
    return receipt


def _account_address_fallback(w3) -> str:
    if not w3.eth.accounts:
        raise RuntimeError("No unlocked node account and BLOCKCHAIN_PRIVATE_KEY is empty.")
    return w3.eth.accounts[0]


# ----------------------------------------------------------------------- record content
def attempt_payload(db: Session, attempt: AssessmentAttempt, version: int) -> Dict[str, Any]:
    answers = db.query(StudentAnswer).filter(StudentAnswer.attempt_id == attempt.id).order_by(StudentAnswer.question_id).all()
    final = lambda a: a.faculty_score_override if a.faculty_score_override is not None else a.score_awarded
    return {
        "v": 1, "version": version, "attempt_id": attempt.id, "assessment_id": attempt.assessment_id,
        "student_id": attempt.student_id, "status": attempt.status,
        "completed_at": attempt.completed_at.isoformat() if attempt.completed_at else None,
        "total_score": round(float(attempt.total_score or 0), 2), "max_score": round(float(attempt.max_score or 0), 2),
        "answers": [{"q": a.question_id, "s": round(float(final(a) or 0), 2)} for a in answers],
    }


def live_hash(db: Session, attempt: AssessmentAttempt, version: int) -> str:
    return keyed_hash(attempt_payload(db, attempt, version))


def _to_bytes32(hexstr: str) -> bytes:
    return bytes.fromhex(hexstr)


# ----------------------------------------------------------------------- anchoring
def anchor_attempt(db: Session, attempt: AssessmentAttempt) -> BlockchainRecord:
    """Creates (or supersedes) the record for the attempt's current grade version and tries to anchor it."""
    version = attempt.grade_version or 1
    rec = db.query(BlockchainRecord).filter_by(attempt_id=attempt.id, version=version).first()
    if rec is None:
        payload = attempt_payload(db, attempt, version)
        rec = BlockchainRecord(
            record_type="assessment_completion", student_id=attempt.student_id, attempt_id=attempt.id, version=version,
            record_ref="rec_" + secrets.token_hex(12), payload_sha256_hash=keyed_hash(payload),
            encrypted_payload=canonical_json(payload), status="pending")
        db.add(rec)
        db.query(BlockchainRecord).filter(BlockchainRecord.attempt_id == attempt.id, BlockchainRecord.version < version,
                                          BlockchainRecord.status != "revoked").update({"status": "superseded"})
        db.commit()
    return try_anchor(db, rec)


class ChainConflict(RuntimeError):
    """A different hash is already anchored under this record id. Never overwritten, never auto-resolved."""


def _lookup(contract, record_ref: str):
    exists, rhash, revoked, ts, reg = contract.functions.getRecord(record_ref).call()
    return bool(exists), bytes(rhash).hex(), bool(revoked)


def _find_anchor_tx(w3, contract, record_ref: str):
    """Locate the RecordAnchored event for a record (used when a transaction mined but its receipt was lost)."""
    try:
        for ev in contract.events.RecordAnchored().get_logs(from_block=0):
            if ev["args"]["recordId"] == record_ref:
                return ev["transactionHash"], int(ev["blockNumber"])
    except Exception as exc:                      # event scan is best-effort; the anchor itself is already proven by getRecord
        log.info("event scan failed for %s: %s", record_ref, exc)
    return None, None


def _hex(h) -> Optional[str]:
    if h is None:
        return None
    x = h.hex() if hasattr(h, "hex") else str(h)
    return x if x.startswith("0x") else "0x" + x


def _is_revert(exc: Exception) -> bool:
    m = (type(exc).__name__ + " " + str(exc)).lower()
    return any(k in m for k in ("contractlogicerror", "reverted", "only registrar", "bad id", "empty hash"))


def try_anchor(db: Session, rec: BlockchainRecord, recheck: bool = False) -> BlockchainRecord:
    """Anchor a record exactly once.

    * pending  -> transient problem (node down / reset / timeout): safe to retry.
    * failed   -> the chain rejected it (revert) or holds a conflicting hash: needs a human, still retryable.
    * anchored -> only after a mined receipt (status 1) AND a read-back of the contract confirming our hash.
    A retry first asks the contract whether the id already exists, so a lost receipt never causes a duplicate
    transaction or a permanent "already anchored" revert loop.  recheck=True also re-validates records that
    are marked anchored (used after a local chain reset).
    """
    try:
        w3, contract, _ = _connect()
        if rec.status == "anchored":
            if not recheck:
                return rec
            exists, onchain, _rev = _lookup(contract, rec.record_ref)
            if exists and onchain == rec.payload_sha256_hash:
                return rec
            if exists:
                raise ChainConflict("The chain holds a different hash for this record id.")
            rec.status, rec.transaction_hash, rec.block_number = "pending", None, None   # lost chain: re-anchor below
            rec.reanchored = True                                                       # transient flag for callers/reporting
        exists, onchain, _rev = _lookup(contract, rec.record_ref)
        if exists:
            if onchain != rec.payload_sha256_hash:
                raise ChainConflict("A different hash is already anchored under this record id; refusing to overwrite it.")
            txh, blk = _find_anchor_tx(w3, contract, rec.record_ref)               # adopt the existing anchor
            rec.transaction_hash, rec.block_number = _hex(txh), blk
        else:
            receipt = _send(contract.functions.anchorRecord(rec.record_ref, _to_bytes32(rec.payload_sha256_hash)))
            ok, onchain, _rev = _lookup(contract, rec.record_ref)                  # read-back: receipts alone are not proof
            if not (ok and onchain == rec.payload_sha256_hash):
                raise RuntimeError("Transaction mined but the contract does not hold the expected hash.")
            rec.transaction_hash, rec.block_number = _hex(receipt.transactionHash), int(receipt.blockNumber)
        rec.status, rec.error_message = "anchored", None
        rec.chain_id, rec.contract_address = int(w3.eth.chain_id), contract.address
    except ChainConflict as exc:
        rec.status, rec.error_message = "failed", str(exc)[:300]
        log.error("anchor conflict for %s: %s", rec.record_ref, exc)
    except Exception as exc:
        rec.status = "failed" if _is_revert(exc) else "pending"
        rec.error_message = str(exc)[:300]
        log.warning("anchoring %s for %s: %s", rec.status, rec.record_ref, rec.error_message)
    db.commit()
    att = db.query(AssessmentAttempt).filter_by(id=rec.attempt_id).first()
    if att:
        att.blockchain_hash, att.blockchain_tx_id = rec.payload_sha256_hash, rec.transaction_hash
        att.integrity_status = "anchored" if rec.status == "anchored" else "pending"
        db.commit()
    return rec


# ----------------------------------------------------------------------- verification
def verify_record(db: Session, rec: BlockchainRecord, verified_by_id: Optional[int]) -> Dict[str, Any]:
    attempt = db.query(AssessmentAttempt).filter_by(id=rec.attempt_id).first()
    result: Dict[str, Any] = {"record_ref": rec.record_ref, "version": rec.version, "stored_hash": rec.payload_sha256_hash,
                              "anchor_status": rec.status, "transaction_hash": rec.transaction_hash,
                              "block_number": rec.block_number}
    live = live_hash(db, attempt, rec.version) if attempt else None
    result["live_hash"] = live
    db_match = live is not None and live == rec.payload_sha256_hash
    chain = {"checked": False}
    if rec.status == "superseded":
        outcome, msg = "SUPERSEDED", "A newer grade version exists (a faculty correction); verify the latest record."
    elif rec.status in ("pending", "failed"):
        outcome = "NOT_ANCHORED" if db_match else "MISMATCH"
        msg = ("The record has NOT been written to the blockchain yet. "
               + ("The database still matches the stored hash." if db_match else "The database no longer matches the stored hash!"))
    else:
        try:
            _, contract, _ = _connect()
            exists, matches, revoked, ts, reg = contract.functions.verifyRecord(
                rec.record_ref, _to_bytes32(live or rec.payload_sha256_hash)).call()
            chain = {"checked": True, "exists": exists, "matches_live_data": matches, "revoked": revoked, "timestamp": ts}
            if not exists:
                outcome = "CHAIN_RESET"
                msg = ("The chain has no entry for this anchored record (the local chain was reset or a different "
                       "contract is configured). An administrator can re-anchor it.")
            elif revoked:
                outcome, msg = "REVOKED", "This record was revoked on-chain."
            elif matches and db_match:
                outcome, msg = "VERIFIED", "The current database record matches the hash anchored on the blockchain."
            else:
                outcome, msg = "MISMATCH", "ALERT: the current database record differs from what was anchored. It has been modified."
        except Exception as exc:
            chain = {"checked": False, "error": str(exc)[:200]}
            if "No contract is deployed" in str(exc):
                outcome = "CHAIN_RESET"
                msg = ("The configured contract no longer exists on the node (the local chain was reset or the contract "
                       "redeployed). The earlier anchor is gone; an administrator can re-anchor it. "
                       + ("Local hash check passed." if db_match else "The local hash check FAILED."))
            else:
                outcome = "CHAIN_UNAVAILABLE"
                msg = ("The blockchain node could not be reached, so on-chain verification was not possible. "
                       + ("Local hash check passed." if db_match else "The local hash check FAILED."))
    result.update({"result": outcome, "message": msg, "chain": chain, "database_matches_stored_hash": db_match})
    rec.verified_status, rec.verified_at = outcome.lower(), utcnow()
    db.add(BlockchainVerification(record_id=rec.id, verified_by_id=verified_by_id, result=outcome, live_hash=live,
                                  chain_hash=rec.payload_sha256_hash, details_json={"chain": chain}))
    db.commit()
    return result


def inspect_contract(w3, contract) -> Dict[str, Any]:
    """Checks the deployed code is really our contract: bytecode equality plus a live interface probe."""
    out: Dict[str, Any] = {}
    art = load_artifact()
    code = bytes(w3.eth.get_code(contract.address))
    out["code_present"] = bool(code)
    expected = art.get("deployedBytecode")
    out["bytecode_matches_artifact"] = (code.hex() == expected[2:].lower()) if expected else None
    try:
        contract.functions.totalAnchored().call()
        contract.functions.verifyRecord("__probe__", b"\x00" * 32).call()
        contract.functions.getRecord("__probe__").call()
        out["interface_ok"] = True
    except Exception as exc:
        out["interface_ok"] = False
        out["interface_error"] = str(exc)[:120]
    return out


def chain_status() -> Dict[str, Any]:
    try:
        w3, contract, _ = _connect()
        return {"connected": True, "chain_id": int(w3.eth.chain_id), "contract": contract.address,
                "total_anchored_on_chain": int(contract.functions.totalAnchored().call()),
                **inspect_contract(w3, contract)}
    except Exception as exc:
        return {"connected": False, "error": str(exc)[:200]}
