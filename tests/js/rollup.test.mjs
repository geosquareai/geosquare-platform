// Hand-checked values for web/map/js/rollup.mjs. Run with: node --test tests/js/rollup.test.mjs
// Needs the local WASM build in web/map/pkg (copied from the engine, not in git).
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const pkg = await import("../../web/map/pkg/geosquare_wasm.js");
await pkg.default({ module_or_path: readFileSync(new URL("../../web/map/pkg/geosquare_wasm_bg.wasm", import.meta.url)) });
const { rollupToLevel, levelForZoom } = await import("../../web/map/js/rollup.mjs");

const PARENT = pkg.encode(9, 4000, 5000);
const [A, B, C, D] = pkg.children(PARENT); // the four level 10 children of one parent

const rules = (extra = {}) => ({ n: { rule: "sum" }, ...extra });

test("sum adds the children", () => {
  const rows = [A, B, C].map((gid, i) => ({ gid, n: i + 1 }));
  assert.equal(rollupToLevel(rows, 9, rules())[0].n, 6);
});

test("max and min take the extreme child", () => {
  const rows = [A, B, C].map((gid, i) => ({ gid, d: [5, 1, 3][i] }));
  const out = rollupToLevel(rows, 9, { d: { rule: "max" } });
  assert.equal(out[0].d, 5);
  assert.equal(rollupToLevel(rows, 9, { d: { rule: "min" } })[0].d, 1);
});

test("NULL children are skipped, not counted as 0", () => {
  const rows = [{ gid: A, n: 4 }, { gid: B, n: null }, { gid: C }];
  assert.equal(rollupToLevel(rows, 9, rules())[0].n, 4);
  assert.equal(rollupToLevel([{ gid: A, n: null }], 9, rules())[0].n, null);
});

test("weighted_mean uses the weight column and skips rows with NULL weight", () => {
  const rows = [
    { gid: A, v: 2, w: 1 },
    { gid: B, v: 6, w: 3 },
    { gid: C, v: 100, w: null }, // no weight: left out
  ];
  const out = rollupToLevel(rows, 9, { v: { rule: "weighted_mean", weight: "w" } });
  assert.equal(out[0].v, (2 * 1 + 6 * 3) / 4); // 5
});

test("weighted_mean with weight area gives every child equal weight", () => {
  const rows = [{ gid: A, v: 2 }, { gid: B, v: 4 }];
  const out = rollupToLevel(rows, 9, { v: { rule: "weighted_mean", weight: "area" } });
  assert.equal(out[0].v, 3);
});

test("median needs at least 3 values and skips NULLs", () => {
  const three = [A, B, C].map((gid, i) => ({ gid, m: [9, 1, 5][i] }));
  assert.equal(rollupToLevel(three, 9, { m: { rule: "median" } })[0].m, 5);
  const even = [A, B, C, D].map((gid, i) => ({ gid, m: [1, 2, 3, 4][i] }));
  assert.equal(rollupToLevel(even, 9, { m: { rule: "median" } })[0].m, 2.5);
  const two = [{ gid: A, m: 1 }, { gid: B, m: 2 }, { gid: C, m: null }];
  assert.equal(rollupToLevel(two, 9, { m: { rule: "median" } })[0].m, null);
  const withNull = [...three, { gid: D, m: null }];
  assert.equal(rollupToLevel(withNull, 9, { m: { rule: "median" } })[0].m, 5);
});

test("rows at the target level stay as they are", () => {
  const out = rollupToLevel([{ gid: PARENT, n: 7 }], 9, rules());
  assert.equal(out[0].gid, PARENT);
  assert.equal(out[0].n, 7);
});

test("levelForZoom maps zoom to the grid level", () => {
  assert.equal(levelForZoom(15), 12);
  assert.equal(levelForZoom(14), 11);
  assert.equal(levelForZoom(12), 10);
  assert.equal(levelForZoom(11), 9);
});
