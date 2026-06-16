import assert from "node:assert/strict";
import test from "node:test";
import { increment } from "../src/counter.js";

test("increment returns the next count", () => {
  assert.equal(increment(2), 3);
});
