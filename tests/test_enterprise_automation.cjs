const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

test("monitor ignores late results from a previous organization and exposes private report URLs", async () => {
    const elements = new Map();
    function element() {
        return { children: [], appendChild(child) { this.children.push(child); },
            replaceChildren() { this.children = []; }, addEventListener() {}, reset() {} };
    }
    function get(id) {
        if (!elements.has(id)) elements.set(id, element());
        return elements.get(id);
    }
    const browser = { window: {}, document: { getElementById: get, createElement: element },
        setTimeout: () => 1, clearTimeout() {} };
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../app/static/js/enterprise-automation.js"), "utf8"), browser);
    const base = "/api/me/organizations/beta/datasets/9/automation";
    let release;
    const empty = { jobs: [], schedules: [], rules: [], alerts: [], reports: [], latest: {} };
    const panel = browser.window.createEnterpriseAutomationPanel((_method, url) => url === base
        ? Promise.resolve({ ...empty, reports: [{ id: 5, parameters: { version: 2 } }] })
        : new Promise(resolve => { release = resolve; }));
    panel.open("/api/me/organizations/alpha/datasets/1/automation", 8, ["forecast_run"]);
    panel.open(base, 2, ["dataset_read"]);
    release({ ...empty, reports: [{ id: 8, parameters: { version: 8 } }] });
    await new Promise(setImmediate);
    assert.equal(get("automationJobForm").hidden, true);
    assert.equal(get("automationReports").children[0].children[0].children[1].href, base + "/reports/5/csv");
    panel.reset();
    assert.equal(get("automationReports").children.length, 0);
});
