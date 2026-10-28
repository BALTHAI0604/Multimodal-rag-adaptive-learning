// Compiles the contract with the bundled solc-js (no internet needed) and writes the ABI+bytecode
// that the Python backend deploys/uses:  blockchain/abi/StudentAcademicAudit.json
const solc = require("solc");
const fs = require("fs");
const path = require("path");
const src = fs.readFileSync(path.join(__dirname, "..", "contracts", "StudentAcademicAudit.sol"), "utf8");
const input = {
  language: "Solidity",
  sources: { "StudentAcademicAudit.sol": { content: src } },
  settings: { optimizer: { enabled: true, runs: 200 }, outputSelection: { "*": { "*": ["abi", "evm.bytecode.object", "evm.deployedBytecode.object"] } } },
};
const out = JSON.parse(solc.compile(JSON.stringify(input)));
const errs = (out.errors || []).filter((e) => e.severity === "error");
if (errs.length) { console.error(errs.map((e) => e.formattedMessage).join("\n")); process.exit(1); }
const c = out.contracts["StudentAcademicAudit.sol"].StudentAcademicAudit;
const dir = path.join(__dirname, "..", "abi");
fs.mkdirSync(dir, { recursive: true });
fs.writeFileSync(path.join(dir, "StudentAcademicAudit.json"),
  JSON.stringify({ contractName: "StudentAcademicAudit", abi: c.abi, bytecode: "0x" + c.evm.bytecode.object, deployedBytecode: "0x" + c.evm.deployedBytecode.object }, null, 2));
console.log("wrote abi/StudentAcademicAudit.json (solc " + solc.version() + ")");
