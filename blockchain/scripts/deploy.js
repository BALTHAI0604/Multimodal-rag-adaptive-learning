const hre = require("hardhat");
const fs = require("fs");
const path = require("path");

async function main() {
  const F = await hre.ethers.getContractFactory("StudentAcademicAudit");
  const c = await F.deploy();
  await c.waitForDeployment();
  const address = await c.getAddress();
  const net = await hre.ethers.provider.getNetwork();
  console.log(`StudentAcademicAudit deployed to: ${address} (chainId ${net.chainId})`);
  const dir = path.join(__dirname, "..", "deployments");
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, "localhost.json"), JSON.stringify({ address, chainId: Number(net.chainId) }, null, 2));
  console.log("Put this in your .env ->  CONTRACT_ADDRESS=" + address);
}
main().catch((e) => { console.error(e); process.exitCode = 1; });
