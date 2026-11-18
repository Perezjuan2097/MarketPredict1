const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const root = path.join(__dirname, "..");
const html = fs.readFileSync(path.join(root, "app/views/index.html"), "utf8");
const js = fs.readFileSync(path.join(root, "app/static/js/script.js"), "utf8");

test("the academic interface exposes exactly six numbered modules in order", () => {
    const bar = html.slice(html.indexOf('<nav class="main-nav module-bar"'), html.indexOf("</nav>", html.indexOf("module-bar")));
    const modules = [...bar.matchAll(/data-nav="([^"]+)"(?: hidden)?><span class="module-index">(\d)<\/span>([^<]+)</g)]
        .map(([, key, index, label]) => [key, Number(index), label.trim()]);
    assert.deepEqual(modules, [
        ["inicio", 1, "Inicio"], ["summary", 2, "Análisis de mercado"], ["predicciones", 3, "Predicciones"],
        ["cuenta", 4, "Mis predicciones"], ["empresas", 5, "Datos empresariales"], ["administracion", 6, "Administración SaaS"],
    ]);
    assert.match(bar, /data-nav="administracion" hidden/);
});

test("every tab panel belongs to one module and every route opens an existing panel", () => {
    const mapping = js.slice(js.indexOf("const MODULE_OF = {"), js.indexOf("};", js.indexOf("const MODULE_OF = {")));
    const panels = [...html.matchAll(/<section id="tab([A-Z][A-Za-z]+)" class="tab-panel/g)].map(([, name]) => name);
    for (const panel of panels) {
        const key = panel === "Summary" ? "summary" : panel.charAt(0).toLowerCase() + panel.slice(1);
        assert.match(mapping, new RegExp("\\b" + key + ":"), "Panel sin módulo: " + panel);
    }
    const routes = js.slice(js.indexOf("const ROUTES = {"), js.indexOf("};", js.indexOf("const ROUTES = {")));
    for (const [, target] of routes.matchAll(/"#[a-z-]+": "([a-z]+)"/g)) {
        const id = target === "summary" ? "tabSummary" : "tab" + target.charAt(0).toUpperCase() + target.slice(1);
        assert.ok(html.includes('id="' + id + '"'), "Ruta hacia un panel inexistente: " + target);
    }
});

test("statistical disclaimers are visible in Inicio and Predicciones", () => {
    assert.match(html, /home-disclaimer[^>]*>[^<]*estimaciones estadísticas/);
    assert.match(html, /limits-callout[^>]*><strong>Limitaciones\.<\/strong>/);
});
