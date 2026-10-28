require("@nomicfoundation/hardhat-toolbox");
const { subtask } = require("hardhat/config");
const { TASK_COMPILE_SOLIDITY_GET_SOLC_BUILD } = require("hardhat/builtin-tasks/task-names");

const SOLC_VERSION = "0.8.20";

// Offline-friendly: use the bundled `solc` (solc-js) npm package when its version matches,
// so `hardhat compile/test/node` work without downloading a native compiler.
subtask(TASK_COMPILE_SOLIDITY_GET_SOLC_BUILD, async (args, hre, runSuper) => {
  if (args.solcVersion === SOLC_VERSION) {
    try {
      const solc = require("solc");
      const longVersion = solc.version();
      if (longVersion.startsWith(SOLC_VERSION)) {
        return {
          compilerPath: require.resolve("solc/soljson.js"),
          isSolcJs: true,
          version: SOLC_VERSION,
          longVersion,
        };
      }
    } catch (_) { /* fall through to the default downloader */ }
  }
  return runSuper();
});

/** Local development only. No private keys live in this file. */
module.exports = {
  solidity: { version: SOLC_VERSION, settings: { optimizer: { enabled: true, runs: 200 } } },
  networks: {
    hardhat: { chainId: 31337 },
    localhost: { url: "http://127.0.0.1:8545", chainId: 31337 },
  },
};
