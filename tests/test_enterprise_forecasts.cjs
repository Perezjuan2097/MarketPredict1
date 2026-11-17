const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../app/static/js/enterprise-forecasts.js"), "utf8");

function fixture(api) {
    class Node {
        constructor() { this.children = []; this.handlers = {}; this.value = ""; }
        appendChild(child) { this.children.push(child); }
        replaceChildren() { this.children = []; }
        addEventListener(event, fn) { this.handlers[event] = fn; }
        async emit(event) { await this.handlers[event]({ preventDefault() {} }); }
        get options() { return this.children; }
        reset() {}
    }
    const nodes = new Map();
    const get = id => {
        if (!nodes.has(id)) nodes.set(id, new Node());
        return nodes.get(id);
    };
    get("forecastForm").elements = { group_by: new Node(), group: new Node() };
    get("forecastForm").fields = { target: "revenue", start: "2025-01-01", complete_through: "2025-03-20",
        horizon: "2", scenario_pct: "10", zero_dates: "2025-01-08, 2025-01-09" };
    const browser = { window: {}, document: { getElementById: get, createElement: () => new Node() },
        FormData: function (form) { return Object.entries(form.fields); } };
    vm.runInNewContext(source, browser);
    return { panel: browser.window.createEnterpriseForecastPanel(api), get };
}

const result = {
    id: 8, state: "ready", parameters: { target: "revenue", version: 4 },
    provenance: { currency: "USD", zero_days_declared: 2, negative_source_rows: 1, outlier_days: 1 },
    result: { baseline: "naive", selected_model: "naive", beats_baseline_on_final: false,
        interval: { coverage_final: 0.75, nominal_level: 0.8, final_points: 4 },
        forecast: [{ date: "2025-03-21", value: 50, low: 30, high: 70 }],
        scenario: { pct: 10, points: [{ date: "2025-03-21", value: 55 }] },
        models: { naive: { selection: { mae: 3 }, final: { mae: 4, rmse: 5, wape: 0.1, bias: 0 } } },
    },
};

test("forecasts submit a fixed dataset version and render real metrics separately from scenarios", async () => {
    const requests = [];
    const url = "/api/me/organizations/acme/datasets/7/forecasts";
    const { panel, get } = fixture(async (method, path, body) => {
        requests.push({ method, path, body });
        if (path.endsWith("?after=0")) return { runs: [] };
        if (method === "POST") return { job: { id: 7, state: "completed", result: { run_id: 8 } } };
        return { run: result };
    });
    await panel.open(url, 4, ["forecast_run"]);
    await get("forecastForm").emit("submit");
    assert.equal(requests[1].path, url);
    assert.equal(requests[1].body.version, 4);
    assert.equal(requests[1].body.horizon, 2);
    assert.deepEqual(Array.from(requests[1].body.zero_dates), ["2025-01-08", "2025-01-09"]);
    assert.match(get("forecastQuality").textContent, /Escenario hipotético/);
    assert.match(get("forecastQuality").textContent, /no aporta evidencia de mejora/);
    assert.equal(get("forecastResult").hidden, false);
    const details = get("forecastPoints").children[0].children[1].children[0].children;
    assert.equal(details.at(-1).textContent, "55");
    panel.reset();
    assert.equal(get("forecastPoints").children.length, 0);
});

test("viewers only read and stale tenant history is ignored", async () => {
    let release;
    const { panel, get } = fixture((_method, url) => url.includes("/alpha/")
        ? new Promise(resolve => { release = resolve; }) : Promise.resolve({ runs: [] }));
    const first = panel.open("/api/me/organizations/alpha/datasets/7/forecasts", 4, ["forecast_run"]);
    await panel.open("/api/me/organizations/beta/datasets/9/forecasts", 2, ["dataset_read"]);
    release({ runs: [result] });
    await first;
    assert.equal(get("forecastForm").hidden, true);
    assert.equal(get("forecastHistory").options.length, 1);
});
