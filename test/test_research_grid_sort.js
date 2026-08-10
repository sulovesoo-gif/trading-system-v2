"use strict";

const assert = require("assert");
global.window = {};
require("../reports/multi-ma/research-grid-sort.js");

const { compareRows } = window.ResearchGridSort;
const number = { field: "value", sortType: "number" };
const date = { field: "when", sortType: "date" };
const string = { field: "name", sortType: "string" };

function sorted(rows, column, direction) {
  return rows.slice().sort((a, b) => compareRows(a, b, column, direction));
}

assert.deepStrictEqual(sorted([{ value: 100 }, { value: 2 }, { value: 10 }], number, "asc").map(x => x.value), [2, 10, 100]);
assert.deepStrictEqual(sorted([{ value: 100 }, { value: 2 }, { value: 10 }], number, "desc").map(x => x.value), [100, 10, 2]);
assert.deepStrictEqual(sorted([{ when: "2026-08-04" }, { when: "2026-01-02" }, { when: "2026-02-01" }], date, "asc").map(x => x.when), ["2026-01-02", "2026-02-01", "2026-08-04"]);
assert.deepStrictEqual(sorted([{ name: null }, { name: "B" }, { name: "A" }], string, "asc").map(x => x.name), ["A", "B", null]);
assert.deepStrictEqual(sorted([{ name: null }, { name: "B" }, { name: "A" }], string, "desc").map(x => x.name), ["B", "A", null]);

console.log("research grid sort: ok");
