"""Blockchain failure modes on a real contract running in an in-memory EVM (eth-tester):
interface/bytecode check, lost receipt (no duplicate anchors), conflicting hash, reverts, node outage, chain reset."""
import pytest
from web3 import EthereumTesterProvider, Web3

from app.blockchain import service as chain
from app.database import SessionLocal
from app.models.models import AssessmentAttempt, BlockchainRecord
from test_02_academic import _complete


def _rec(db, ref):
    return db.query(BlockchainRecord).filter_by(record_ref=ref).first()


def _pending_record(client, H, published):
    """Complete an attempt while no chain is configured -> a genuine 'pending' record."""
    chain.reset()
    _complete(client, H, published["id"])
    return client.get("/api/blockchain/records", headers=H("s1@t.edu")).json()[0]


def _fresh_chain():
    w3 = Web3(EthereumTesterProvider())
    art = chain.load_artifact()
    h = w3.eth.contract(abi=art["abi"], bytecode=art["bytecode"]).constructor().transact({"from": w3.eth.accounts[0]})
    c = w3.eth.contract(address=w3.eth.wait_for_transaction_receipt(h).contractAddress, abi=art["abi"])
    return w3, c


def test_contract_interface_and_bytecode_are_verified(eth):
    st = chain.chain_status()
    assert st["connected"] and st["code_present"] and st["interface_ok"]
    assert st["bytecode_matches_artifact"] is True          # deployed runtime code == compiled artifact


def test_wrong_contract_is_detected(eth):
    w3, c = eth
    # point the service at an address that holds no contract code
    bogus = w3.eth.contract(address=w3.eth.accounts[3], abi=chain.load_artifact()["abi"])
    chain.set_test_chain(w3, bogus, None)
    st = chain.chain_status()
    assert st["connected"] is False and "No contract is deployed" in st["error"]


def test_lost_receipt_retry_adopts_existing_anchor_without_duplicate(client, world, H, published, eth):
    w3, contract = eth
    rec0 = _pending_record(client, H, published)
    chain.set_test_chain(w3, contract, None)
    db = SessionLocal()
    rec = _rec(db, rec0["record_ref"])
    # simulate: the transaction was mined but the API never saw the receipt
    contract.functions.anchorRecord(rec.record_ref, bytes.fromhex(rec.payload_sha256_hash)).transact({"from": w3.eth.accounts[0]})
    before = contract.functions.totalAnchored().call()
    rec = chain.try_anchor(db, rec)
    assert rec.status == "anchored" and rec.error_message is None
    assert rec.transaction_hash and rec.transaction_hash.startswith("0x") and rec.block_number is not None
    assert contract.functions.totalAnchored().call() == before == 1          # no second transaction was sent
    chain.try_anchor(db, rec)                                                # idempotent
    assert contract.functions.totalAnchored().call() == 1
    db.close()


def test_conflicting_onchain_hash_is_never_overwritten(client, world, H, published, eth):
    w3, contract = eth
    rec0 = _pending_record(client, H, published)
    chain.set_test_chain(w3, contract, None)
    contract.functions.anchorRecord(rec0["record_ref"], b"\x11" * 32).transact({"from": w3.eth.accounts[0]})
    db = SessionLocal()
    rec = chain.try_anchor(db, _rec(db, rec0["record_ref"]))
    assert rec.status == "failed" and "different hash" in rec.error_message
    assert contract.functions.getRecord(rec.record_ref).call()[1] == b"\x11" * 32
    att = db.get(AssessmentAttempt, rec.attempt_id)
    assert att.integrity_status != "anchored"
    db.close()


def test_revert_is_failed_then_retry_succeeds_after_fix(client, world, H, published, eth):
    w3, contract = eth
    rec0 = _pending_record(client, H, published)
    chain.set_test_chain(w3, contract, None)
    owner = w3.eth.accounts[0]
    contract.functions.setRegistrar(owner, False).transact({"from": owner})          # node account no longer authorised
    db = SessionLocal()
    rec = chain.try_anchor(db, _rec(db, rec0["record_ref"]))
    assert rec.status == "failed" and "registrar" in rec.error_message.lower()
    assert contract.functions.totalAnchored().call() == 0
    contract.functions.setRegistrar(owner, True).transact({"from": owner})
    res = client.post("/api/blockchain/retry-pending", headers=H("admin@t.edu")).json()
    assert res == {"tried": 1, "anchored": 1}
    db.expire_all()
    assert _rec(db, rec0["record_ref"]).status == "anchored"
    db.close()


def test_node_unavailable_stays_pending_and_verify_reports_it(client, world, H, published, eth, monkeypatch):
    w3, contract = eth
    _complete(client, H, published["id"])
    chain.set_test_chain(w3, contract, None)
    chain.try_anchor(SessionLocal(), SessionLocal().query(BlockchainRecord).first())
    ref = client.get("/api/blockchain/records", headers=H("s1@t.edu")).json()[0]["record_ref"]
    # node goes away
    monkeypatch.setattr(w3.eth, "get_code", lambda *a, **k: (_ for _ in ()).throw(ConnectionError("node down")))
    v = client.post("/api/blockchain/verify", headers=H("s1@t.edu"), json={"record_ref": ref}).json()
    assert v["result"] == "CHAIN_UNAVAILABLE" and "could not be reached" in v["message"]
    assert chain.chain_status()["connected"] is False
    assert chain._contract is None                      # stale handle dropped so the next call reconnects cleanly


def test_chain_reset_is_detected_and_records_are_reanchored(client, world, H, published, eth):
    w3, contract = eth
    _complete(client, H, published["id"])
    chain.set_test_chain(w3, contract, None)
    db = SessionLocal()
    rec = db.query(BlockchainRecord).first()
    chain.try_anchor(db, rec)
    ref = rec.record_ref
    assert _rec(db, ref).status == "anchored"
    # --- the local chain is reset and the contract redeployed (same deployer => same address, empty state)
    w3b, contract_b = _fresh_chain()
    assert contract_b.address == contract.address
    chain.set_test_chain(w3b, contract_b, None)
    v = client.post("/api/blockchain/verify", headers=H("s1@t.edu"), json={"record_ref": ref}).json()
    assert v["result"] == "CHAIN_RESET"                 # not VERIFIED, not silently MISMATCH
    assert contract_b.functions.totalAnchored().call() == 0
    # --- and a reset where the contract is NOT redeployed must not fake an anchor
    w3c = Web3(EthereumTesterProvider())
    chain.set_test_chain(w3c, w3c.eth.contract(address=contract.address, abi=chain.load_artifact()["abi"]), None)
    rec = db.query(BlockchainRecord).filter_by(record_ref=ref).first()
    new = client.get("/api/blockchain/records", headers=H("s1@t.edu")).json()[0]
    assert new["status"] == "anchored"                 # old claim is only corrected by an explicit admin re-check
    out = client.post(f"/api/blockchain/records/{ref}/retry", headers=H("admin@t.edu")).json()
    assert out["status"] == "pending" and "No contract is deployed" in out["error"]
    # --- contract redeployed: admin retry re-anchors exactly once
    chain.set_test_chain(w3b, contract_b, None)
    assert client.post("/api/blockchain/retry-pending", headers=H("admin@t.edu")).json() == {"tried": 1, "anchored": 1}
    assert contract_b.functions.totalAnchored().call() == 1
    assert client.post("/api/blockchain/retry-pending", headers=H("admin@t.edu")).json() == {"tried": 0, "anchored": 0}
    client.post(f"/api/blockchain/records/{ref}/retry", headers=H("admin@t.edu"))
    assert contract_b.functions.totalAnchored().call() == 1
    assert client.post("/api/blockchain/verify", headers=H("s1@t.edu"), json={"record_ref": ref}).json()["result"] == "VERIFIED"
    db.close()


def test_anchor_requires_receipt_and_readback(client, world, H, published, eth, monkeypatch):
    """If a transaction 'succeeds' but the contract does not hold our hash, the record must NOT be marked anchored."""
    w3, contract = eth
    rec0 = _pending_record(client, H, published)
    chain.set_test_chain(w3, contract, None)
    monkeypatch.setattr(chain, "_lookup", lambda c, ref: (False, "", False))
    db = SessionLocal()
    rec = chain.try_anchor(db, _rec(db, rec0["record_ref"]))
    assert rec.status == "pending" and "does not hold the expected hash" in rec.error_message
    db.close()
