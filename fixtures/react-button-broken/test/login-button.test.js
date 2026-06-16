import assert from "node:assert/strict";
import test from "node:test";
import { getSubmitButtonState } from "../src/login-button.js";

test("submit button is disabled while login is loading", () => {
  const state = getSubmitButtonState({ loading: true });

  assert.equal(state.disabled, true);
});
