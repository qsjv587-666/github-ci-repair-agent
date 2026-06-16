import assert from "node:assert/strict";
import test from "node:test";
import { getActiveTodos } from "../src/todos.js";

test("getActiveTodos returns only incomplete todos", () => {
  const todos = [
    { title: "ship", completed: false },
    { title: "archive", completed: true }
  ];

  assert.deepEqual(getActiveTodos(todos), [{ title: "ship", completed: false }]);
});
