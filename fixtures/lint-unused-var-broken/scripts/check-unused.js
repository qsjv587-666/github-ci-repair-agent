import { readFileSync } from "node:fs";

const source = readFileSync(new URL("../src/greeting.js", import.meta.url), "utf8");

if (source.includes("unusedMessage")) {
  console.error("/workspace/src/greeting.js");
  console.error("  2:9  error  'unusedMessage' is assigned a value but never used  no-unused-vars");
  process.exit(1);
}

console.log("lint passed");
