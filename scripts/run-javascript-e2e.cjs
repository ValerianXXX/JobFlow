"use strict";

const {spawnSync} = require("node:child_process");
const {readdirSync} = require("node:fs");
const {join, resolve} = require("node:path");

const projectRoot = resolve(__dirname, "..");
const testsRoot = join(projectRoot, "tests");
const suites = readdirSync(testsRoot)
  .filter((name) => name.endsWith("e2e.cjs"))
  .sort();

if (suites.length === 0) {
  process.stderr.write("JOBFLOW_JAVASCRIPT_E2E_MISSING\n");
  process.exit(2);
}

for (const suite of suites) {
  process.stdout.write(`[RUN] ${suite}\n`);
  const result = spawnSync(process.execPath, [join(testsRoot, suite)], {
    cwd: projectRoot,
    encoding: "utf8",
    stdio: "inherit",
    windowsHide: true,
  });
  if (result.error) {
    process.stderr.write("JOBFLOW_JAVASCRIPT_E2E_LAUNCH_FAILED\n");
    process.exit(2);
  }
  if (result.status !== 0) {
    process.exit(typeof result.status === "number" ? result.status : 2);
  }
}

process.stdout.write(`JOBFLOW_JAVASCRIPT_E2E_PASS=${suites.length}\n`);
