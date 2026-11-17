const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../app/static/js/enterprise-datasets.js"), "utf8");

function fixture(api) {
    class Node {
        constructor() {
            this.children = [];
            this.handlers = {};
            this.elements = { name: { value: "" } };
            this.value = "";
        }
        appendChild(child) { this.children.push(child); }
        replaceChildren() { this.children = []; }
        addEventListener(event, fn) { this.handlers[event] = fn; }
        async emit(event) { await this.handlers[event]({ target: this, preventDefault() {} }); }
        get options() { return this.children; }
        reset() {}
    }
    const nodes = new Map();
    const element = id => {
        if (!nodes.has(id)) nodes.set(id, new Node());
        return nodes.get(id);
    };
    const browser = { document: { getElementById: element, createElement: () => new Node() }, window: {} };
    vm.runInNewContext(source, browser);
    return { panel: browser.window.createDatasetPanel(api), element };
}

test("dashboard loads actual metrics and records scoped to the selected version and tenant", async () => {
    const base = "/api/me/organizations/acme/datasets";
    const requests = [];
    const version = { version: 2, status: "ready", uploaded_at: "2026-11-17", format: "csv",
        quality: { valid_rows: 3, missing_cells: { category: 2 }, revenue_discrepancies: 0 } };
    const responses = {
        [base + "?after=0"]: { datasets: [{ id: 5, name: "Ventas" }] },
        [base + "/5/versions"]: { versions: [version] },
        [base + "/5"]: { dataset: { name: "Ventas", currency: "USD" }, version: 2,
            metrics: { records: 3, sales: "15.5", units: "7", products: 2 }, series: [] },
        [base + "/5/rows?version=2&after=0"]: { rows: [
            { row_number: 2, date: "2026-11-17", product: "<b>Literal</b>", quantity: "7" },
        ] },
    };
    const { panel, element } = fixture(async (method, url) => {
        requests.push([method, url]);
        assert.ok(responses[url], url);
        return responses[url];
    });
    await panel.open(base, ["dataset_read"]);
    element("datasetSelect").value = "5";
    await element("datasetSelect").emit("change");
    assert.equal(element("datasetDetail").hidden, false);
    assert.equal(element("datasetImport").hidden, true);
    assert.equal(element("datasetRename").hidden, true);
    assert.equal(element("datasetMetrics").children[1].children[1].textContent, "15.5");
    assert.match(element("datasetQuality").textContent, /3 filas válidas/);
    const cells = element("datasetRows").children[0].children[1].children[0].children;
    assert.equal(cells[1].textContent, "<b>Literal</b>");
    assert.ok(requests.every(([method, url]) => method === "GET" && url.startsWith(base)));
    panel.reset();
    assert.equal(element("enterpriseDatasets").hidden, true);
    assert.equal(element("datasetRows").children.length, 0);
});

test("a late response from the previous organization never replaces the active dataset list", async () => {
    let resolvePrevious;
    const { panel, element } = fixture((_method, url) => {
        if (url.includes("/alpha/")) return new Promise(resolve => { resolvePrevious = resolve; });
        return Promise.resolve({ datasets: [{ id: 8, name: "Beta private" }] });
    });
    const pending = panel.open("/api/me/organizations/alpha/datasets", ["dataset_read"]);
    await panel.open("/api/me/organizations/beta/datasets", ["dataset_read", "dataset_manage"]);
    resolvePrevious({ datasets: [{ id: 9, name: "Alpha private" }] });
    await pending;
    assert.equal(element("datasetCreate").hidden, false);
    assert.deepEqual(element("datasetSelect").children.map(option => option.textContent),
        ["Selecciona un dataset", "Beta private"]);
});
