"""Recorrido de extremo a extremo de la versión académica con Playwright.

Se ejecuta en CI contra un servidor web y un worker reales (PostgreSQL desechable y datos de Yahoo Finance).
Cada paso es un caso de prueba con identificador; su resultado, duración y captura quedan en ``--out``.
Los datos empresariales son sintéticos y están marcados DEMO. Si una fuente externa falla, el caso se
registra como fallido con el mensaje real: nunca se sustituyen datos.

Uso: python -m scripts.academic_e2e --base http://127.0.0.1:8000 --out evidencia
"""
import argparse
from datetime import datetime, timezone
from io import StringIO
import json
import os
from pathlib import Path
from secrets import token_hex
import time

from playwright.sync_api import TimeoutError as PlaywrightTimeout, expect, sync_playwright

from scripts.synthetic_pilot import generate

# bypass_csp: la CSP de la aplicación prohíbe eval, y Playwright evalúa sus esperas como texto JavaScript.
# Solo afecta al navegador de prueba; la aplicación y sus cabeceras no cambian.
DESKTOP = {"width": 1440, "height": 900}
MOBILE = {"width": 390, "height": 844}
PASSWORD = "Demo" + token_hex(6) + "9"


class Recorder:
    def __init__(self, out):
        self.out = Path(out)
        (self.out / "capturas").mkdir(parents=True, exist_ok=True)
        self.cases = []

    def shot(self, page, name, full=False):
        path = self.out / "capturas" / f"{name}.png"
        page.screenshot(path=str(path), full_page=full)
        return path.name

    def case(self, ident, module, title, fn, page=None):
        start = time.monotonic()
        entry = {"id": ident, "module": module, "title": title}
        try:
            detail = fn()
            entry.update(status="aprobada", detail=detail or "")
        except Exception as exc:  # se registra el fallo real y el recorrido continúa
            entry.update(status="fallida", detail=f"{type(exc).__name__}: {str(exc)[:400]}")
            if page is not None:  # diagnóstico: estado visible y captura del fallo
                try:
                    entry["status_text"] = page.locator("#status").inner_text(timeout=2000)[:300]
                    page.screenshot(path=str(self.out / "capturas" / f"FALLO_{ident}.png"), full_page=True)
                except Exception:
                    pass
        entry["seconds"] = round(time.monotonic() - start, 2)
        self.cases.append(entry)
        print(f"[{entry['status']}] {ident} {title} ({entry['seconds']} s) {entry['detail'][:160]}", flush=True)
        return entry["status"] == "aprobada"


def go(page, nav):
    page.click(f'.module-bar a[data-nav="{nav}"]')
    page.wait_for_timeout(400)


def wait_text(page, selector, pattern, timeout=60000):
    page.wait_for_function(
        "([s, p]) => { const n = document.querySelector(s); return n && new RegExp(p).test(n.textContent); }",
        arg=[selector, pattern], timeout=timeout)


def register(page, base, username):
    page.goto(base + "/#inicio")
    page.wait_for_selector('[data-home="register"]', timeout=20000)
    page.click('[data-home="register"]')
    form = page.locator("#registerForm")
    form.locator('input[name="username"]').fill(username)
    form.locator('input[name="email"]').fill(username + "@example.test")
    form.locator('input[name="password"]').fill(PASSWORD)
    form.locator('button[type="submit"]').click()
    page.wait_for_function(f"document.querySelector('#authButton').textContent.trim() === {json.dumps(username)}",
                           timeout=20000)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--models", default="AAPL,MSFT,^GSPC,BTC-USD")
    args = parser.parse_args()
    base = args.base.rstrip("/")
    rec = Recorder(args.out)
    user = "estudiante_demo"
    meta = {"started_at": datetime.now(timezone.utc).isoformat(), "base": base, "user": user,
            "browser": None, "viewport_desktop": DESKTOP, "viewport_mobile": MOBILE}
    model_runs = {}

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        meta["browser"] = "Chromium " + browser.version
        ctx = browser.new_context(viewport=DESKTOP, locale="es-CO", bypass_csp=True)
        page = ctx.new_page()
        console_errors = []
        page.on("pageerror", lambda exc: console_errors.append(str(exc)))

        def home_visitor():
            page.goto(base + "/")
            page.wait_for_selector("#tabInicio.active-panel", timeout=20000)
            expect(page.locator(".module-bar a[data-nav]:visible")).to_have_count(5)
            expect(page.locator('.module-bar a[data-nav="administracion"]')).to_be_hidden()
            page.wait_for_function("!document.querySelector('#homeStatus .loading')", timeout=30000)
            page.wait_for_function("!document.querySelector('#homeMarkets .loading')", timeout=60000)
            rec.shot(page, "01_inicio_visitante", full=True)
            return page.locator("#homeMarkets").inner_text()[:200]
        rec.case("CP-UI-01", "Inicio", "Inicio carga sin sesión, con 5 módulos visibles y administración oculta", home_visitor)

        def do_register():
            page.click('[data-home="register"]')
            page.wait_for_selector("#registerForm:visible")
            rec.shot(page, "02_registro")
            page.keyboard.press("Escape")
            register(page, base, user)
            go(page, "inicio")
            page.wait_for_function("document.querySelector('#homeAuth').textContent.includes('Sesión iniciada')")
            page.wait_for_function("!document.querySelector('#homeAuth .loading')", timeout=20000)
            rec.shot(page, "03_inicio_con_sesion", full=True)
            return "cuenta creada y sesión iniciada"
        rec.case("CP-UI-02", "Inicio", "Registro e inicio de sesión desde Inicio", do_register)

        def search():
            go(page, "summary")
            page.fill("#globalSearch", "apple")
            page.wait_for_selector("#searchSuggestions:not(.hidden) .suggestion", timeout=30000)
            rec.shot(page, "04_busqueda_sugerencias")
            page.keyboard.press("Escape")
            return f"{page.locator('#searchSuggestions .suggestion').count()} sugerencias"
        rec.case("CP-UI-03", "Análisis de mercado", "Búsqueda de activos con sugerencias", search)

        def analyze():
            go(page, "summary")
            page.fill("#symbol", "AAPL")
            page.click(".analyze-button")
            wait_text(page, "#status", "Análisis completado|Datos de mercado actualizados", timeout=120000)
            expect(page.locator("#cards .card").first).to_be_visible()
            rec.shot(page, "05_analisis_aapl", full=True)
            return page.locator("#status").inner_text()
        rec.case("CP-UI-04", "Análisis de mercado", "Análisis de AAPL con indicadores, tendencia y gráfico", analyze, page)

        def period():
            page.click('.period[data-period="1A"]')
            wait_text(page, "#status", "Datos de mercado actualizados", timeout=60000)
            page.wait_for_timeout(600)
            page.locator(".chart-panel").screenshot(path=str(rec.out / "capturas" / "06_grafico_1A.png"))
            return "periodo 1A"
        rec.case("CP-UI-05", "Análisis de mercado", "Cambio de periodo del gráfico histórico", period)

        for ident, tab, shot, ready in (
            ("CP-UI-06", "technical", "07_datos_tecnicos", "#technicalContent > :not(.loading)"),
            ("CP-UI-07", "seasonal", "08_estacionalidad", "#seasonalTable > :not(.loading)"),
            ("CP-UI-08", "news", "09_noticias", "#newsContent > :not(.loading)"),
            ("CP-UI-09", "mercados", "10_mercados", "#marketsContent > :not(.loading)"),
        ):
            def open_tab(tab=tab, shot=shot, ready=ready):
                page.click(f'.context-tab[data-tab="{tab}"]')
                page.wait_for_selector(ready, timeout=90000)
                rec.shot(page, shot, full=True)
                return tab
            rec.case(ident, "Análisis de mercado", f"Pestaña {tab} carga datos o informa el error", open_tab)

        def predictions():
            go(page, "predicciones")
            page.select_option("#modelsHorizon", "5")
            page.click("#runModels")
            page.wait_for_function("!document.querySelector('#modelsContent .loading')", timeout=300000)
            if page.locator("#modelsContent .empty-state").count() and not page.locator("#modelsContent table").count():
                raise RuntimeError(page.locator("#modelsContent").inner_text()[:300])
            page.wait_for_selector("#projectionPanel:not([hidden])", timeout=60000)
            page.wait_for_timeout(800)
            rec.shot(page, "11_predicciones_resultado", full=True)
            page.locator("#projectionPanel").screenshot(path=str(rec.out / "capturas" / "12_proyeccion.png"))
            page.click("#modelsContent details summary")
            rec.shot(page, "13_predicciones_detalle_tecnico", full=True)
            return page.locator("#modelsContent").inner_text()[:200]
        rec.case("CP-UI-10", "Predicciones", "Ejecución de modelos, comparación, métricas y proyección", predictions)

        def save():
            page.click("#saveModels")
            page.wait_for_timeout(1500)
            go(page, "cuenta")
            page.wait_for_selector("#predList table tbody tr", timeout=30000)
            rec.shot(page, "14_mis_predicciones", full=True)
            return f"{page.locator('#predList table tbody tr').count()} filas guardadas"
        rec.case("CP-UI-11", "Mis predicciones", "Guardado de la emisión firmada y consulta del historial", save)

        def results_validation():
            go(page, "predicciones")
            page.locator("#runResults").scroll_into_view_if_needed()
            rec.shot(page, "15_validacion_multiactivo")
            return "panel de validación visible"
        rec.case("CP-UI-12", "Predicciones", "Panel de validación en varios activos accesible", results_validation)

        csv_path = rec.out / "demo_ventas_sinteticas.csv"
        buffer = StringIO()
        generate(buffer, 120)
        csv_path.write_text(buffer.getvalue(), encoding="utf-8")

        def enterprise():
            go(page, "empresas")
            page.wait_for_selector("#enterpriseContent:not([hidden])", timeout=20000)
            form = page.locator("#enterpriseCreate")
            form.locator('input[name="name"]').fill("DEMO Comercializadora Andina")
            form.locator('input[name="slug"]').fill("demo-andina")
            form.locator('button[type="submit"]').click()
            page.wait_for_selector("#enterpriseDashboard:not([hidden])", timeout=20000)
            page.wait_for_selector("#enterpriseDatasets:not([hidden])", timeout=20000)
            page.locator("#datasetCreate input[name='name']").fill("DEMO Ventas diarias")
            page.locator("#datasetCreate button[type='submit']").click()
            page.wait_for_selector("#datasetDetail:not([hidden])", timeout=20000)
            rec.shot(page, "16_empresa_y_dataset", full=True)
            return "organización y dataset creados"
        rec.case("CP-UI-13", "Datos empresariales", "Creación de organización y dataset", enterprise)

        def import_data():
            page.set_input_files("#datasetFile", str(csv_path))
            page.click("#datasetInspect")
            page.wait_for_selector("#datasetMapping:not([hidden])", timeout=20000)
            for key, column in (("date", "fecha"), ("product", "producto"), ("quantity", "cantidad"),
                                ("price", "precio"), ("category", "categoria"), ("transaction_id", "tx")):
                page.select_option(f'#datasetFields select[name="{key}"]', column)
            rec.shot(page, "17_mapeo_columnas", full=True)
            page.locator("#datasetMapping button[type='submit']").click()
            page.wait_for_selector("#datasetConfirm:not([hidden])", timeout=20000)
            rec.shot(page, "18_validacion_previa", full=True)
            page.click("#datasetConfirm")
            wait_text(page, "#datasetMessage", "Versión 1 disponible", timeout=30000)
            page.wait_for_selector("#datasetMetrics .card, #datasetMetrics article", timeout=20000)
            rec.shot(page, "19_version_e_indicadores", full=True)
            return page.locator("#datasetMessage").inner_text()
        rec.case("CP-UI-14", "Datos empresariales", "Importación CSV con mapeo, validación y versión", import_data)

        def enterprise_forecast():
            page.wait_for_selector("#enterpriseForecasts:not([hidden])", timeout=20000)
            form = page.locator("#forecastForm")
            form.locator('input[name="start"]').fill("2025-01-01")
            form.locator('input[name="complete_through"]').fill("2025-04-30")
            form.locator('input[name="horizon"]').fill("3")
            form.locator('button[type="submit"]').click()
            page.wait_for_selector("#forecastResult:not([hidden])", timeout=180000)
            page.wait_for_timeout(800)
            rec.shot(page, "20_pronostico_empresarial", full=True)
            return page.locator("#forecastMessage").inner_text()
        rec.case("CP-UI-15", "Datos empresariales", "Pronóstico empresarial procesado por el worker", enterprise_forecast)

        def automation():
            page.click("#automationRefresh")
            page.wait_for_timeout(1500)
            page.locator("#enterpriseAutomation").screenshot(path=str(rec.out / "capturas" / "21_automatizacion.png"))
            return page.locator("#automationSummary").inner_text()[:200]
        rec.case("CP-UI-16", "Datos empresariales", "Panel de monitoreo y automatizaciones", automation)

        def restricted_admin():
            page.goto(base + "/#administracion")
            page.wait_for_selector("#tabAdministracion.active-panel", timeout=20000)
            wait_text(page, "#adminContent", "Acceso restringido")
            rec.shot(page, "22_administracion_restringida")
            status = page.request.get(base + "/admin/api/commercial").status
            if status not in (401, 403, 302):
                raise RuntimeError(f"La API administrativa respondió {status} a un usuario sin rol de administrador")
            return f"interfaz restringida y API {status}"
        rec.case("CP-SEC-01", "Administración SaaS", "Un usuario común no accede a la administración", restricted_admin)

        # Ventas/organizaciones de otra cuenta: aislamiento entre empresas.
        other = browser.new_context(viewport=DESKTOP, locale="es-CO", bypass_csp=True)
        other_page = other.new_page()

        def isolation():
            register(other_page, base, "otra_empresa_demo")
            status = other_page.request.get(base + "/api/me/organizations/demo-andina").status
            if status != 404:
                raise RuntimeError(f"Otra cuenta obtuvo {status} al pedir la organización ajena")
            return "organización ajena responde 404"
        rec.case("CP-SEC-02", "Datos empresariales", "Otra cuenta no puede ver la organización de un tercero", isolation)

        admin_ctx = browser.new_context(viewport=DESKTOP, locale="es-CO", bypass_csp=True)
        admin_page = admin_ctx.new_page()

        def admin_module():
            register(admin_page, base, "docente_admin")
            from app.db import get_engine
            from app.repositories.sql import SQLRepository
            repo = SQLRepository(get_engine())
            admin_id = admin_page.request.get(base + "/api/auth/me").json()["user"]["id"]
            repo.admin_update_user(admin_id, None, role="admin")
            admin_page.goto(base + "/#administracion")
            admin_page.reload()  # recarga la sesión con el rol nuevo
            page_ready = admin_page.locator('.module-bar a[data-nav="administracion"]')
            expect(page_ready).to_be_visible(timeout=20000)
            admin_page.wait_for_selector("#adminContent table", timeout=30000)
            admin_page.wait_for_timeout(500)
            rec.shot(admin_page, "23_administracion_saas", full=True)
            return admin_page.locator("#adminContent .metric-grid").inner_text()[:200]
        rec.case("CP-UI-17", "Administración SaaS", "Administrador global consulta organizaciones, cuotas y workers", admin_module)

        def api_error():
            err = browser.new_context(viewport=DESKTOP, locale="es-CO", bypass_csp=True)
            err_page = err.new_page()
            err_page.route("**/api/market-data/**", lambda route: route.fulfill(
                status=503, content_type="application/json",
                body=json.dumps({"error": "La fuente de datos de mercado no respondió."})))
            err_page.goto(base + "/#analisis")
            wait_text(err_page, "#status", "no respondió", timeout=30000)
            rec.shot(err_page, "24_error_fuente_externa")
            text = err_page.locator("#status").inner_text()
            err.close()
            return text
        rec.case("CP-UI-18", "Análisis de mercado", "Fallo simulado de la fuente externa: se informa sin datos falsos", api_error)

        def mobile():
            mob = browser.new_context(viewport=MOBILE, device_scale_factor=2, is_mobile=True, has_touch=True, locale="es-CO", bypass_csp=True)
            mp = mob.new_page()
            mp.goto(base + "/")
            mp.wait_for_selector("#tabInicio.active-panel", timeout=20000)
            mp.wait_for_selector("#homeMarkets table, #homeMarkets .empty-state", state="attached", timeout=60000)
            if mp.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1"):
                raise RuntimeError("La página de Inicio desborda horizontalmente en móvil")
            rec.shot(mp, "25_movil_inicio", full=True)
            mp.locator('.module-bar a[data-nav="summary"]').click()
            mp.wait_for_selector("#tabSummary.active-panel", timeout=20000)
            mp.wait_for_timeout(2500)
            if mp.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1"):
                raise RuntimeError("Análisis de mercado desborda horizontalmente en móvil")
            rec.shot(mp, "26_movil_analisis", full=True)
            mp.locator('.module-bar a[data-nav="predicciones"]').click()
            mp.wait_for_timeout(500)
            rec.shot(mp, "27_movil_predicciones")
            mob.close()
            return "sin desbordamiento horizontal"
        rec.case("CP-UI-19", "Transversal", "Diseño responsive en 390 × 844", mobile)

        def dark():
            page.goto(base + "/#analisis")
            page.click("#themeToggle")
            page.wait_for_timeout(2500)
            rec.shot(page, "28_tema_oscuro")
            page.click("#themeToggle")
            return "tema oscuro"
        rec.case("CP-UI-20", "Transversal", "Tema oscuro legible", dark)

        def logout():
            page.goto(base + "/#inicio")
            page.click("#authButton")
            page.click("#accountMenuLogout")
            page.wait_for_function("document.querySelector('#authButton').textContent.trim() === 'Empiece'", timeout=20000)
            return "sesión cerrada"
        rec.case("CP-UI-21", "Transversal", "Cierre de sesión", logout)

        # Ejecuciones reales del motor para las tablas y gráficos del documento.
        for symbol in [s for s in args.models.split(",") if s]:
            def run_model(symbol=symbol):
                response = page.request.get(f"{base}/api/models/{symbol}?horizon=5", timeout=300000)
                body = response.json()
                if response.status != 200:
                    raise RuntimeError(body.get("error", response.status))
                body.pop("emission", None)
                model_runs[symbol] = body
                return f"{len(body.get('models', []))} modelos, consenso {body.get('consensus', {}).get('status')}"
            rec.case(f"CP-ML-{symbol}", "Predicciones", f"Motor predictivo sobre {symbol} a 5 sesiones", run_model)

        meta["console_errors"] = console_errors
        other.close()
        admin_ctx.close()
        ctx.close()
        browser.close()

    meta["finished_at"] = datetime.now(timezone.utc).isoformat()
    meta["passed"] = sum(c["status"] == "aprobada" for c in rec.cases)
    meta["failed"] = sum(c["status"] == "fallida" for c in rec.cases)
    (rec.out / "e2e_resultados.json").write_text(json.dumps({"meta": meta, "cases": rec.cases}, indent=2, ensure_ascii=False),
                                                encoding="utf-8")
    (rec.out / "modelos_ejecutados.json").write_text(json.dumps(model_runs, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: meta[k] for k in ("passed", "failed")}))


if __name__ == "__main__":
    main()
