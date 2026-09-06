// Executes the report page's real sorting script against a minimal document
// model built from the real rendered page, and checks the resulting row order.
//
// Lineuparr groups rows into one collapsible section per score band or status,
// each with its own table, so this harness models EVERY table on the page and
// exercises the first and the last. A script that wires up only the first table
// leaves the other sections' headers looking sortable and doing nothing, which
// is a defect this harness exists to catch.
//
// Usage: node report_sort_harness.js <rendered-page.html> <sort-script.js>
const fs = require("fs");

const HTML = fs.readFileSync(process.argv[2], "utf8");

const tableBlocks = [...HTML.matchAll(/<table>([\s\S]*?)<\/table>/g)].map((m) => m[1]);

function parseTable(block) {
  const headerLabels = [...block.matchAll(/<th class="sortable"[^>]*>([^<]*)<\/th>/g)]
    .map((m) => m[1]);
  const tbody = block.match(/<tbody>([\s\S]*?)<\/tbody>/);
  const rowData = tbody
    ? [...tbody[1].matchAll(/<tr>([\s\S]*?)<\/tr>/g)].map((m) =>
        [...m[1].matchAll(/<td data-v="([^"]*)">([^<]*)<\/td>/g)].map((c) => ({
          dataV: c[1],
          text: c[2],
        }))
      )
    : [];
  return { headerLabels, rowData };
}

const parsed = tableBlocks.map(parseTable);

if (parsed.length < 2 || parsed.some((t) => !t.headerLabels.length || !t.rowData.length)) {
  console.log(
    "HARNESS-BROKEN: needed at least two tables each with headers and rows, got " +
      parsed.map((t) => t.headerLabels.length + "h/" + t.rowData.length + "r").join(", ")
  );
  process.exit(2);
}

function makeCell(cell) {
  return {
    getAttribute: (name) => (name === "data-v" ? cell.dataV : null),
    textContent: cell.text,
  };
}

function makeTable({ headerLabels, rowData }) {
  const rows = rowData.map((cells) => ({ children: cells.map(makeCell) }));
  const body = {
    rows: rows.slice(),
    appendChild(row) {
      const at = this.rows.indexOf(row);
      if (at !== -1) this.rows.splice(at, 1);
      this.rows.push(row);
    },
  };
  const headers = headerLabels.map((label) => {
    const attrs = { "aria-sort": "none" };
    const listeners = {};
    return {
      label,
      getAttribute: (n) => (n in attrs ? attrs[n] : null),
      setAttribute: (n, v) => {
        attrs[n] = v;
      },
      addEventListener: (type, fn) => {
        (listeners[type] = listeners[type] || []).push(fn);
      },
      fire: (type, event) => (listeners[type] || []).forEach((fn) => fn(event)),
    };
  });
  return {
    tBodies: [body],
    body,
    headers,
    headerLabels,
    querySelectorAll: (sel) => (sel === "th.sortable" ? headers : []),
  };
}

const tables = parsed.map(makeTable);

global.document = {
  querySelector: (sel) => (sel === "table" ? tables[0] : null),
  querySelectorAll: (sel) => (sel === "table" ? tables : []),
};

// eval is deliberate and is the entire purpose of this harness: it executes the
// plugin's own sorting script, read from the plugin source tree, so the code
// being verified is the code that ships. There is no untrusted input here; the
// argument is a path this repository controls. This file is a local test
// harness and is not shipped.
eval(fs.readFileSync(process.argv[3], "utf8"));

function column(table, index) {
  return table.body.rows.map((r) => r.children[index].textContent);
}

const first = tables[0];
const last = tables[tables.length - 1];
const numberIndex = first.headerLabels.indexOf("Number");
const channelIndex = first.headerLabels.indexOf("Channel");

if (numberIndex === -1 || channelIndex === -1) {
  console.log("HARNESS-BROKEN: the fixture page has no Number or Channel column");
  process.exit(2);
}

console.log("tables parsed  : " + tables.length);
console.log("headers        : " + first.headerLabels.join(", "));
console.log("initial order  : " + column(first, numberIndex).join(", "));

first.headers[numberIndex].fire("click");
const ascendingNumeric = column(first, numberIndex);
console.log("1 click Number : " + ascendingNumeric.join(", ") +
  " | aria-sort = " + first.headers[numberIndex].getAttribute("aria-sort"));

first.headers[numberIndex].fire("click");
const descendingNumeric = column(first, numberIndex);
console.log("2 clicks Number: " + descendingNumeric.join(", ") +
  " | aria-sort = " + first.headers[numberIndex].getAttribute("aria-sort"));

first.headers[channelIndex].fire("click");
const ascendingText = column(first, channelIndex);
console.log("click Channel  : " + ascendingText.join(", ") +
  " | previous header reset to " + first.headers[numberIndex].getAttribute("aria-sort"));

let prevented = false;
first.headers[channelIndex].fire("keydown", {
  key: "Enter",
  preventDefault: () => (prevented = true),
});
console.log("Enter Channel  : " + column(first, channelIndex).join(", ") +
  " | preventDefault called = " + prevented);

// The last section's table. Its rows are deliberately out of order in the
// fixture, so an unsorted result here means its headers were never wired up.
const lastBefore = column(last, numberIndex);
last.headers[numberIndex].fire("click");
const lastAfter = column(last, numberIndex);
console.log("last section   : " + lastBefore.join(", ") + "  ->  " + lastAfter.join(", "));

const sortedAscending = (values) =>
  JSON.stringify(values) ===
  JSON.stringify(values.slice().sort((a, b) => Number(a) - Number(b)));

const checks = [
  ["numbers sort as numbers, not as text",
    JSON.stringify(ascendingNumeric) === JSON.stringify(["2", "3", "10"])],
  ["a second click reverses the order",
    JSON.stringify(descendingNumeric) === JSON.stringify(["10", "3", "2"])],
  ["text sorts ignoring letter case",
    JSON.stringify(ascendingText) === JSON.stringify(["alpha", "Bravo", "Charlie"])],
  ["sorting a new column resets the previous one",
    first.headers[numberIndex].getAttribute("aria-sort") === "none"],
  ["the keyboard path sorts and suppresses the default action", prevented === true],
  ["the last section's table sorts too, not only the first",
    lastAfter.length > 1 && sortedAscending(lastAfter)],
];

console.log("");
let allPassed = true;
for (const [name, passed] of checks) {
  console.log((passed ? "PASS  " : "FAIL  ") + name);
  if (!passed) allPassed = false;
}
process.exit(allPassed ? 0 : 1);
