const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const app = path.join(__dirname, "..", "app");
const script = fs.readFileSync(path.join(app, "static/js/script.js"), "utf8");
const page = fs.readFileSync(path.join(app, "views/index.html"), "utf8");
const styles = fs.readFileSync(path.join(app, "static/css/style.css"), "utf8");

async function fixture({ authenticated = true, brokerAllowed = false, hash = "", logoutOk = true, role = "user", enterprise = false } = {}) {
    const fixtureState = { focused: null, requests: [] };

    function element(attributes = {}) {
        const listeners = new Map();
        const classes = new Set();
        const attrs = new Map(Object.entries(attributes));
        return {
            hidden: attrs.has("hidden"),
            dataset: {},
            classList: {
                add: (name) => classes.add(name),
                remove: (name) => classes.delete(name),
                contains: (name) => classes.has(name),
                toggle: (name, on) => on ? classes.add(name) : classes.delete(name)
            },
            addEventListener(name, fn) {
                if (!listeners.has(name)) listeners.set(name, []);
                listeners.get(name).push(fn);
            },
            async emit(name, event = {}) {
                for (const fn of listeners.get(name) || []) {
                    await fn({ target: this, preventDefault() {}, ...event });
                }
            },
            getAttribute: (key) => attrs.get(key) ?? null,
            setAttribute: (key, value) => attrs.set(key, value),
            removeAttribute: (key) => attrs.delete(key),
            focus() { fixtureState.focused = this; },
            closest() { return null; },
            replaceChildren() { this.innerHTML = ""; }
        };
    }

    const trigger = element();
    const menu = element({ hidden: "" });
    const profile = element();
    const administration = element({ href: "/admin/", hidden: "" });
    const signOut = element();
    const error = element({ role: "alert" });
    const title = element({ tabindex: "-1" });
    const account = element();
    const summary = element();
    const brokerPanel = element();
    const brokerContent = element();
    const brokerLink = element({ href: "#brokers", hidden: "" });
    brokerLink.dataset.nav = "brokers";
    summary.classList.add("active-panel");
    const wrapper = { contains: (target) => [trigger, menu, profile, administration, signOut, error].includes(target) };
    const nodes = {
        "#authButton": trigger, "#accountMenu": menu, "#accountMenuProfile": profile,
        "#accountMenuLogout": signOut, "#accountMenuError": error, "#accountDisclosure": wrapper,
        "#accountMenuAdmin": administration,
        "#accountTitle": title, "#tabCuenta": account, "#tabSummary": summary, "#tabBrokers": brokerPanel,
        "#brokerContent": brokerContent, '[data-nav="brokers"]': brokerLink, "#pfEmpty": element(), "#pfArea": element()
    };
    for (const id of ["runModels", "saveModels", "modelsContent", "modelsHorizon"]) {
        nodes["#" + id] = element();
    }
    nodes["#modelsHorizon"].value = "5";
    const dialog = element();
    dialog.showModal = () => { dialog.open = true; };
    for (const id of ["authDialog", "authTabLogin", "authTabRegister", "authError", "loginForm", "registerForm"]) {
        nodes["#" + id] = id === "authDialog" ? dialog : element();
    }
    if (enterprise) {
        for (const id of ["tabEmpresas", "enterpriseContent", "enterpriseSignIn", "enterpriseLogin",
            "enterpriseDashboard", "enterpriseSelect", "enterpriseMore", "enterpriseMoreMembers",
            "enterpriseMessage", "enterpriseName", "enterpriseRole", "enterpriseDirectory",
            "enterpriseNewRole", "enterpriseMembers", "enterpriseSettings", "enterpriseCreate", "enterpriseAdd"]) {
            nodes["#" + id] = element();
        }
        nodes["#enterpriseSettings"].elements = { timezone: {}, currency: {} };
        nodes["#enterpriseSettings"].querySelector = () => element();
        for (const id of ["enterpriseCreate", "enterpriseSettings", "enterpriseAdd"]) {
            nodes["#" + id].reset = () => {};
        }
    }
    const document = element();
    document.querySelector = (selector) => nodes[selector] ?? null;
    document.querySelectorAll = (selector) => ({
        ".tab-panel": [account, summary, brokerPanel, nodes["#tabEmpresas"]].filter(Boolean),
        ".main-nav a[data-nav]": [brokerLink]
    }[selector] || []);
    const responses = {
        "/api/auth/me": [true, { user: authenticated ? { username: "Fixture", role } : null,
            csrf: "fixture-csrf", persistence: true, can_access_broker: brokerAllowed }],
        "/api/models/AAPL?horizon=5": [true, { asset: "AAPL", data_source: "demo", horizon_days: 5,
            as_of: "2025-01-01", data_points: 300, models: [], baselines: [], notes: [],
            emission: { token: "signed-forecast" } }],
        "/api/news/AAPL": [true, {}],
        "/api/me/predictions/from-models": [true, { saved: [{ id: 1 }] }],
        "/api/auth/logout": [logoutOk, { csrf: "new-csrf" }],
        "/api/me/portfolios": [true, { portfolios: [] }],
        "/api/broker/status": [true, { authorized: true }],
        "/api/market-data/AAPL?period=1D": [false, {}]
    };
    if (enterprise) {
        const organization = { id: 15, slug: "fixture-org", name: "Fixture Company", role: "owner",
            permissions: ["read", "members", "write_settings", "manage_members", "manage_privileged"] };
        const url = "/api/me/organizations";
        responses[url] = [true, { organization }];
        responses[url + "?after=0"] = [true, { organizations: [] }];
        responses[url + "/fixture-org"] = [true, { organization }];
        responses[url + "/fixture-org/settings"] = [true, { settings: { timezone: "UTC", currency: "USD" } }];
        responses[url + "/fixture-org/members?after=0"] = [true, {
            members: [{ user_id: 41, username: "Fixture User", role: "owner", blocked: false }],
        }];
    }
    const context = {
        document, location: { hash }, window: { addEventListener() {} },
        localStorage: { getItem: () => null },
        FormData: function (form) { return Object.entries(form.formFields); },
        fetch: async (url, options = {}) => {
            fixtureState.requests.push({ url, options });
            assert.ok(responses[url], "Unexpected request for " + url);
            const [ok, body] = responses[url];
            return { ok, status: ok ? 200 : 503, json: async () => body };
        }
    };
    vm.runInNewContext(script, context, { filename: "script.js" });
    await document.emit("DOMContentLoaded");
    await new Promise(setImmediate);
    return { fixtureState, trigger, menu, profile, signOut, title, error, account, summary,
        brokerPanel, brokerLink, administration, dialog, document, nodes };
}

test("the template uses native buttons and a hidden disclosure with an alert", () => {
    for (const id of ["authButton", "accountMenuProfile", "accountMenuLogout"]) {
        assert.match(page, new RegExp('<button[^>]*id="' + id + '"'));
    }
    assert.match(page, /id="accountMenu"[^>]*\bhidden\b/);
    assert.match(page, /id="accountMenuError"[^>]*role="alert"/);
    assert.match(page, /id="accountTitle" tabindex="-1"/);
});

test("responsive header reserves space for account controls without hiding them", () => {
    assert.match(styles, /@media \(max-width: 1100px\)\s*\{\s*\.topbar\s*\{[^}]*grid-template-areas: "brand controls" "search search"/);
    assert.match(styles, /@media \(max-width: 800px\)\s*\{\s*\.topbar\s*\{[^}]*grid-template-areas: "brand controls"/);
    assert.match(styles, /\.topbar-right\s*\{[^}]*grid-area: controls/);
    for (const rule of styles.matchAll(/\.(?:topbar-right|account-disclosure)(?:\s*>\s*\.start-button)?\s*\{([^}]*)\}/g)) {
        assert.doesNotMatch(rule[1], /display\s*:\s*none\b|visibility\s*:\s*hidden\b/);
    }
    assert.match(styles, /\.account-disclosure > \.start-button\s*\{[^}]*max-width: 100%[^}]*text-overflow: ellipsis/);
});

test("the local Chart.js bundle keeps the runtime version and has no source map reference", () => {
    const asset = "vendor/chartjs-4.5.0/chart.umd.min.js";
    assert.ok(page.includes('src="/static/' + asset + '"'));
    const chartSource = fs.readFileSync(path.join(app, "static", asset), "utf8");
    assert.doesNotMatch(chartSource, /sourceMappingURL=/);
    const browser = {};
    vm.runInNewContext(chartSource, browser);
    assert.equal(browser.Chart.version, "4.5.0");
});

test("authenticated disclosure toggles and closes with Escape, outside click and My account", async () => {
    const f = await fixture();
    assert.equal(f.trigger.getAttribute("aria-controls"), "accountMenu");
    assert.equal(f.trigger.getAttribute("aria-expanded"), "false");
    await f.trigger.emit("click");
    assert.equal(f.menu.hidden, false);
    assert.equal(f.trigger.getAttribute("aria-expanded"), "true");
    await f.document.emit("click", { target: f.profile });
    assert.equal(f.menu.hidden, false);
    await f.trigger.emit("click");
    assert.equal(f.menu.hidden, true);
    await f.trigger.emit("click");
    await f.document.emit("keydown", { key: "Escape" });
    assert.equal(f.menu.hidden, true);
    assert.equal(f.trigger.getAttribute("aria-expanded"), "false");
    assert.equal(f.fixtureState.focused, f.trigger);
    await f.trigger.emit("click");
    await f.document.emit("click", { target: f.summary });
    assert.equal(f.menu.hidden, true);
    await f.trigger.emit("click");
    await f.profile.emit("click");
    assert.equal(f.menu.hidden, true);
    assert.equal(f.account.classList.contains("active-panel"), true);
    assert.equal(f.fixtureState.focused, f.title);
});

test("logout posts CSRF, clears UI on success and preserves it on failure", async () => {
    for (const logoutOk of [true, false]) {
        const f = await fixture({ brokerAllowed: true, logoutOk });
        await f.trigger.emit("click");
        await f.signOut.emit("click");
        assert.equal(f.menu.hidden, true);
        assert.equal(f.fixtureState.focused, f.trigger);
        const requests = f.fixtureState.requests.filter(({ url }) => url === "/api/auth/logout");
        assert.equal(requests.length, 1);
        assert.equal(requests[0].options.method, "POST");
        assert.equal(requests[0].options.headers["X-CSRF-Token"], "fixture-csrf");
        assert.equal(f.trigger.textContent, logoutOk ? "Empiece" : "Fixture");
        assert.equal(f.brokerLink.hidden, logoutOk);
        assert.equal(f.trigger.getAttribute("aria-expanded"), logoutOk ? null : "false");
        assert.equal(Boolean(f.error.textContent), !logoutOk);
        if (!logoutOk) {
            assert.match(f.error.textContent, /No se pudo cerrar sesión/);
            await f.trigger.emit("click");
            assert.equal(f.menu.hidden, false);
        }
    }
});

test("broker deep link is fail-closed and anonymous account opens login", async () => {
    const guest = await fixture({ authenticated: false, hash: "#brokers" });
    await guest.trigger.emit("click");
    assert.equal(guest.dialog.open, true);
    assert.equal(guest.menu.hidden, true);
    assert.equal(guest.trigger.getAttribute("aria-expanded"), null);
    assert.equal(guest.brokerLink.hidden, true);
    const denied = await fixture({ hash: "#brokers" });
    assert.equal(denied.brokerLink.hidden, true);
    assert.equal(denied.brokerPanel.classList.contains("active-panel"), false);
    for (const f of [guest, denied]) {
        assert.equal(f.fixtureState.requests.some(({ url }) => url === "/api/broker/status"), false);
    }
    const allowed = await fixture({ brokerAllowed: true, hash: "#brokers" });
    assert.equal(allowed.brokerLink.hidden, false);
    assert.equal(allowed.brokerPanel.classList.contains("active-panel"), true);
    assert.equal(allowed.fixtureState.requests.filter(({ url }) => url === "/api/broker/status").length, 1);
});

test("only an account administrator sees the administration link", async () => {
    const anonymous = await fixture({ authenticated: false, brokerAllowed: true });
    const member = await fixture();
    assert.equal(anonymous.administration.hidden, true);
    assert.equal(member.administration.hidden, true);
    const admin = await fixture({ role: "admin" });
    await admin.trigger.emit("click");
    assert.equal(admin.administration.hidden, false);
    await admin.administration.emit("click");
    assert.equal(admin.menu.hidden, true);
    await admin.trigger.emit("click");
    await admin.signOut.emit("click");
    assert.equal(admin.administration.hidden, true);
});

test("model saving sends the displayed token and requires a matching horizon", async () => {
    const { nodes, fixtureState } = await fixture();
    const saves = () => fixtureState.requests.filter(({ url }) => url === "/api/me/predictions/from-models");
    await nodes["#saveModels"].emit("click");
    assert.equal(saves().length, 0);
    await nodes["#runModels"].emit("click");
    await nodes["#saveModels"].emit("click");
    assert.equal(saves().length, 1);
    assert.deepEqual(JSON.parse(saves()[0].options.body), { emission_token: "signed-forecast" });
    assert.equal(saves()[0].options.headers["X-CSRF-Token"], "fixture-csrf");
    nodes["#modelsHorizon"].value = "10";
    await nodes["#saveModels"].emit("click");
    assert.equal(saves().length, 1);
});

test("enterprise deep link loads after authentication and opens the newly created organization", async () => {
    const { nodes, fixtureState, signOut } = await fixture({ hash: "#empresas", enterprise: true });
    assert.equal(nodes["#enterpriseSignIn"].hidden, true);
    assert.equal(nodes["#enterpriseContent"].hidden, false);
    assert.equal(nodes["#enterpriseDashboard"].hidden, true);
    const create = nodes["#enterpriseCreate"];
    create.formFields = { name: "Fixture Company", slug: "fixture-org" };
    await create.emit("submit");
    await new Promise(setImmediate);
    const request = fixtureState.requests.find(({ url, options }) =>
        url === "/api/me/organizations" && options.method === "POST");
    assert.deepEqual(JSON.parse(request.options.body), create.formFields);
    assert.equal(request.options.headers["X-CSRF-Token"], "fixture-csrf");
    assert.equal(nodes["#enterpriseDashboard"].hidden, false);
    assert.equal(nodes["#enterpriseName"].textContent, "Fixture Company");
    assert.match(nodes["#enterpriseMembers"].innerHTML, /Fixture User/);
    await signOut.emit("click");
    assert.equal(nodes["#enterpriseContent"].hidden, true);
    assert.equal(nodes["#enterpriseMembers"].innerHTML, "");
});

test("the unauthenticated enterprise deep link shows sign-in without loading tenant data", async () => {
    const { nodes, fixtureState } = await fixture({ authenticated: false, hash: "#empresas", enterprise: true });
    assert.equal(nodes["#enterpriseSignIn"].hidden, false);
    assert.equal(nodes["#enterpriseContent"].hidden, true);
    assert.ok(fixtureState.requests.every(({ url }) => !url.startsWith("/api/me/organizations")));
});
