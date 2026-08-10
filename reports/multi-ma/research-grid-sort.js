/* Common, read-only table sorting for research dashboards. */
(function (root) {
  "use strict";
  var stockNames = {};

  function setStockNames(names) {
    stockNames = names && typeof names === "object" ? names : {};
  }

  function stockLabel(code) {
    var key = code === null || code === undefined ? "" : String(code);
    var name = stockNames[key];
    return name ? name + "(" + key + ")" : key;
  }

  function stockSortKey(code) {
    var key = code === null || code === undefined ? "" : String(code);
    return (stockNames[key] || key) + "\u0000" + key;
  }

  function decorateStockRow(row) {
    var result = Object.assign({}, row);
    result.trade_stock_label = stockLabel(row.trade_stock_code);
    result.trade_stock_sort = stockSortKey(row.trade_stock_code);
    var entryCode = row.signal_source_stock_code;
    var exitCode = row.exit_signal_source_stock_code;
    result.signal_source_label = exitCode && exitCode !== entryCode
      ? stockLabel(entryCode) + " \u2192 " + stockLabel(exitCode)
      : stockLabel(entryCode);
    result.signal_source_sort = stockSortKey(entryCode) + "\u0000" + (exitCode ? stockSortKey(exitCode) : "");
    return result;
  }

  function isMissing(value) {
    return value === null || value === undefined || value === "";
  }

  function sortValue(row, column) {
    var value = row[column.field];
    if (isMissing(value)) return null;
    if (column.sortType === "number") {
      var numeric = Number(value);
      return Number.isFinite(numeric) ? numeric : null;
    }
    if (column.sortType === "date" || column.sortType === "datetime") {
      var timestamp = Date.parse(value);
      return Number.isFinite(timestamp) ? timestamp : null;
    }
    return String(value);
  }

  function compareRows(left, right, column, direction) {
    var a = sortValue(left, column);
    var b = sortValue(right, column);
    // Missing values are always at the end, regardless of sort direction.
    if (a === null && b === null) return 0;
    if (a === null) return 1;
    if (b === null) return -1;
    var result = typeof a === "string" ? a.localeCompare(b, "ko") : a - b;
    return direction === "desc" ? -result : result;
  }

  function bind(options) {
    var table = options.table;
    var columns = options.columns;
    var rows = options.rows;
    var render = options.render;
    var state = options.initial || null;
    var headers = Array.prototype.slice.call(table.querySelectorAll("thead th"));

    function draw() {
      var output = rows().slice();
      if (state) output.sort(function (a, b) {
        return compareRows(a, b, columns[state.index], state.direction);
      });
      render(output);
      headers.forEach(function (header, index) {
        var column = columns[index];
        if (!column) return;
        header.textContent = column.label + (state && state.index === index
          ? (state.direction === "asc" ? " \u25b2" : " \u25bc") : "");
        header.setAttribute("aria-sort", state && state.index === index
          ? (state.direction === "asc" ? "ascending" : "descending") : "none");
      });
    }

    headers.forEach(function (header, index) {
      if (!columns[index]) return;
      header.classList.add("sortable");
      header.addEventListener("click", function () {
        state = state && state.index === index
          ? { index: index, direction: state.direction === "asc" ? "desc" : "asc" }
          : { index: index, direction: "asc" };
        draw();
      });
    });
    return { draw: draw, getState: function () { return state; } };
  }

  root.ResearchGridSort = {
    bind: bind, compareRows: compareRows, sortValue: sortValue,
    setStockNames: setStockNames, stockLabel: stockLabel,
    stockSortKey: stockSortKey, decorateStockRow: decorateStockRow
  };
}(window));
