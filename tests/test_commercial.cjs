const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

test("commercial panel displays count and worker metadata without editing in recovery mode", async () => {
    const nodes = new Map();
    const element = () => ({
        children: [], appendChild(child) { this.children.push(child); },
        replaceChildren() { this.children = []; }, addEventListener() {},
    });
    const get = id => {
        if (!nodes.has(id)) nodes.set(id, element());
        return nodes.get(id);
    };
    get("commercialPanel").dataset = { writable: "false", csrf: "fixture" };
    const data = { web_ready: true, queue: { pending: 2, failed: 1, oldest_due_seconds: 4 },
        workers: [{ state: "stale", age_seconds: 22 }], jobs: [], events: [],
        organizations: [{ id: 3, name: "Demo", slug: "demo", status: "active", plan: "Pilot",
            usage: { datasets: 1 }, limits: { datasets: 3 } }], plans: { Pilot: { datasets: 3 } } };
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../app/static/js/commercial.js"), "utf8"), {
        document: { getElementById: get, createElement: element },
        fetch: async url => {
            assert.equal(url, "/admin/api/commercial?limit=30&after=0");
            return { ok: true, status: 200, json: async () => data };
        },
    });
    await new Promise(setImmediate);
    assert.match(get("commercialServices").textContent, /stale \(22 s\)/);
    const cells = get("commercialOrganizations").children[0].children[1].children[0].children;
    assert.equal(cells[3].textContent, "Pilot");
    assert.equal(cells[4].textContent, "Datasets 1/3");
    assert.equal(get("commercialPlans").children[1].children[0].children[0].readOnly, true);
});
