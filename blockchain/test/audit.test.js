const { expect } = require("chai");
const { ethers } = require("hardhat");

describe("StudentAcademicAudit", () => {
  let c, owner, other;
  const H = ethers.keccak256(ethers.toUtf8Bytes("payload"));
  beforeEach(async () => {
    [owner, other] = await ethers.getSigners();
    c = await (await ethers.getContractFactory("StudentAcademicAudit")).deploy();
  });
  it("anchors and verifies", async () => {
    await c.anchorRecord("rec_1", H);
    const r = await c.verifyRecord("rec_1", H);
    expect(r.exists && r.matches && !r.revoked).to.equal(true);
  });
  it("detects a modified hash", async () => {
    await c.anchorRecord("rec_1", H);
    const r = await c.verifyRecord("rec_1", ethers.keccak256(ethers.toUtf8Bytes("tampered")));
    expect(r.exists).to.equal(true);
    expect(r.matches).to.equal(false);
  });
  it("rejects non-registrars and duplicates", async () => {
    await expect(c.connect(other).anchorRecord("rec_2", H)).to.be.revertedWith("only registrar");
    await c.anchorRecord("rec_2", H);
    await expect(c.anchorRecord("rec_2", H)).to.be.revertedWith("already anchored");
  });
  it("revokes", async () => {
    await c.anchorRecord("rec_3", H);
    await c.revokeRecord("rec_3");
    expect((await c.verifyRecord("rec_3", H)).revoked).to.equal(true);
  });
});
