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

window.ResearchGridSort.setStockNames({
  "000660": "SK hynix",
  "0193T0": "KODEX SK hynix leverage",
  "0197X0": "SOL SK hynix inverse 2X"
});
assert.strictEqual(window.ResearchGridSort.stockLabel("000660"), "SK hynix(000660)");
assert.strictEqual(window.ResearchGridSort.stockLabel("123456"), "123456");
const decorated = window.ResearchGridSort.decorateStockRow({
  trade_stock_code: "000660", signal_source_stock_code: "0193T0",
  exit_signal_source_stock_code: "0197X0"
});
assert.strictEqual(decorated.signal_source_label, "KODEX SK hynix leverage(0193T0) \u2192 SOL SK hynix inverse 2X(0197X0)");
const stockName = { field: "trade_stock_sort", sortType: "string" };
assert.deepStrictEqual(sorted([
  window.ResearchGridSort.decorateStockRow({ trade_stock_code: "000660" }),
  window.ResearchGridSort.decorateStockRow({ trade_stock_code: "0193T0" })
], stockName, "asc").map(x => x.trade_stock_code), ["0193T0", "000660"]);

console.log("research grid sort: ok");
