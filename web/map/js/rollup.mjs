// Vendored copy of geosquare-engine bindings/wasm/js/rollup.mjs (commit 393c858c). Keep it in sync
// with the engine. It imports ../pkg, which is web/map/pkg here, so the file must stay in web/map/js.
// Zoom rollup for the browser. Folds rows at a fine level into their ancestor cells at a
// coarser level. Parent cells come from the Rust core (`parent`, `decode`). The value rules
// follow the DuckDB rollup in bindings/duckdb/src/dataset.rs, so browser and server agree.
import { decode, parent } from "../pkg/geosquare_wasm.js";

const RULES = new Set(["sum", "weighted_mean", "median", "max", "min"]);
const NEEDS_WEIGHT = new Set(["weighted_mean"]);

/**
 * Grid level for a map zoom (plan/11, section 5b): zoom 15+ gives level 12, zoom 14 gives 11,
 * zoom 12 to 13 gives 10, and zoom 11 or lower gives 9.
 * shortcut: thresholds are approximate for Indonesia, about 10 px per cell side, upgrade by
 * tuning on real maps. The result is not clamped to the dataset level; the caller does that.
 */
export function levelForZoom(zoom) {
  if (!Number.isFinite(zoom)) throw new Error("zoom must be a finite number");
  if (zoom >= 15) return 12;
  if (zoom >= 14) return 11;
  if (zoom >= 12) return 10;
  return 9;
}

/**
 * Roll rows up to `level`. Returns one `{ gid, [column]: value | null }` per parent GID,
 * sorted by GID.
 *
 * - `rows`: `[{ gid, [column]: number | null | undefined }]`. Rows may be at any level at or
 *   below `level`. Null or undefined values are skipped, never counted as 0.
 * - `rules`: `{ [column]: { rule, weight? } }`. `rule` is one of `sum`, `weighted_mean`,
 *   `median`, `max`, `min`. `weight` is a column name, or `"area"` for
 *   equal weight 1 (all cells at one level have the same area). Weighted rules need a weight.
 *   Other rules take none.
 *
 * A parent with no usable value is `null`. `median` needs at least 3 values, and returns `null` below that.
 */
export function rollupToLevel(rows, level, rules) {
  if (!Number.isInteger(level) || level < 0) throw new Error(`level must be a whole number, got ${level}`);
  checkRules(rules);
  const groups = new Map();
  for (const row of rows) {
    const key = ancestorAt(row.gid, level);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(row);
  }
  return [...groups.keys()].sort().map((gid) => {
    const out = { gid };
    for (const [column, spec] of Object.entries(rules)) {
      out[column] = aggregate(groups.get(gid), column, spec);
    }
    return out;
  });
}

function checkRules(rules) {
  for (const [column, { rule, weight } = {}] of Object.entries(rules)) {
    if (column === "gid") throw new Error('"gid" cannot be a value column');
    if (!RULES.has(rule)) {
      throw new Error(`column ${column}: unknown rule ${rule}; use sum, weighted_mean, median, max or min`);
    }
    if (NEEDS_WEIGHT.has(rule) && weight === undefined) throw new Error(`column ${column}: ${rule} needs a weight`);
    if (!NEEDS_WEIGHT.has(rule) && weight !== undefined) throw new Error(`column ${column}: ${rule} takes no weight`);
  }
}

// Climbs from `gid` with the core `parent` until the level is `level`.
function ancestorAt(gid, level) {
  let current = gid;
  let currentLevel = decode(gid).level;
  if (level > currentLevel) {
    throw new Error(`level ${level} is finer than the row level ${currentLevel} of ${gid}`);
  }
  while (currentLevel > level) {
    current = parent(current);
    currentLevel -= 1;
  }
  return current;
}

function aggregate(group, column, { rule, weight }) {
  const items = [];
  for (const row of group) {
    const value = row[column];
    if (value == null) continue;
    if (typeof value !== "number" || !Number.isFinite(value)) {
      throw new Error(`column ${column}: values must be finite numbers or null, got ${value}`);
    }
    const w = weight === undefined || weight === "area" ? 1 : row[weight];
    if (NEEDS_WEIGHT.has(rule)) {
      if (w == null) continue;
      if (typeof w !== "number" || !Number.isFinite(w) || w < 0) {
        throw new Error(`column ${column}: weight ${weight} must be a finite number of 0 or more, got ${w}`);
      }
    }
    items.push({ value, w });
  }
  if (items.length === 0) return null;

  switch (rule) {
    case "sum":
      return items.reduce((total, item) => total + item.value, 0);
    case "max":
      return items.reduce((best, item) => Math.max(best, item.value), -Infinity);
    case "min":
      return items.reduce((best, item) => Math.min(best, item.value), Infinity);
    case "weighted_mean": {
      const totalWeight = items.reduce((total, item) => total + item.w, 0);
      if (totalWeight === 0) return null;
      return items.reduce((total, item) => total + item.value * item.w, 0) / totalWeight;
    }
    case "median": {
      if (items.length < 3) return null;
      const sorted = items.map((item) => item.value).sort((a, b) => a - b);
      const mid = sorted.length >> 1;
      return sorted.length % 2 === 1 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
    }
    default:
      throw new Error(`unknown rule ${rule}`);
  }
}
