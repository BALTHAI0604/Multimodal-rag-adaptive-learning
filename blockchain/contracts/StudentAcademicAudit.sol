// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/**
 * @title StudentAcademicAudit
 * @notice Anchors KEYED HASHES (HMAC) of academic records. No marks, names, e-mails or transcripts
 *         are ever stored on-chain. Record ids are opaque random references.
 * @dev Only addresses approved by the owner (the institution's registrar service) may anchor or revoke.
 */
contract StudentAcademicAudit {
    struct Proof {
        bytes32 recordHash;
        uint64 timestamp;
        address registrar;
        bool revoked;
    }

    address public owner;
    mapping(address => bool) public registrars;
    mapping(string => Proof) private proofs;
    uint256 public totalAnchored;

    event RecordAnchored(string recordId, bytes32 recordHash, uint64 timestamp, address registrar);
    event RecordRevoked(string recordId, address revoker);
    event RegistrarChanged(address registrar, bool allowed);

    modifier onlyOwner() { require(msg.sender == owner, "only owner"); _; }
    modifier onlyRegistrar() { require(registrars[msg.sender], "only registrar"); _; }

    constructor() {
        owner = msg.sender;
        registrars[msg.sender] = true;
        emit RegistrarChanged(msg.sender, true);
    }

    function setRegistrar(address who, bool allowed) external onlyOwner {
        registrars[who] = allowed;
        emit RegistrarChanged(who, allowed);
    }

    function transferOwnership(address newOwner) external onlyOwner {
        require(newOwner != address(0), "zero address");
        owner = newOwner;
    }

    function anchorRecord(string calldata recordId, bytes32 recordHash) external onlyRegistrar {
        require(bytes(recordId).length > 0 && bytes(recordId).length <= 64, "bad id");
        require(recordHash != bytes32(0), "empty hash");
        require(proofs[recordId].timestamp == 0, "already anchored");
        proofs[recordId] = Proof(recordHash, uint64(block.timestamp), msg.sender, false);
        totalAnchored += 1;
        emit RecordAnchored(recordId, recordHash, uint64(block.timestamp), msg.sender);
    }

    function revokeRecord(string calldata recordId) external onlyRegistrar {
        require(proofs[recordId].timestamp != 0, "not found");
        proofs[recordId].revoked = true;
        emit RecordRevoked(recordId, msg.sender);
    }

    /// @return exists whether the id was anchored, matches whether the supplied hash equals the anchored one
    function verifyRecord(string calldata recordId, bytes32 currentHash)
        external view returns (bool exists, bool matches, bool revoked, uint64 timestamp, address registrar)
    {
        Proof memory p = proofs[recordId];
        if (p.timestamp == 0) return (false, false, false, 0, address(0));
        return (true, p.recordHash == currentHash, p.revoked, p.timestamp, p.registrar);
    }

    function getRecord(string calldata recordId)
        external view returns (bool exists, bytes32 recordHash, bool revoked, uint64 timestamp, address registrar)
    {
        Proof memory p = proofs[recordId];
        return (p.timestamp != 0, p.recordHash, p.revoked, p.timestamp, p.registrar);
    }
}
