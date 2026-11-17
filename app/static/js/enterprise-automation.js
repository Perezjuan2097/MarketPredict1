/* Private monitoring and scheduling for the selected immutable dataset version. */
window.createEnterpriseAutomationPanel = function (api, readForecast) {
    const node = id => document.getElementById(id);
    const groups = ["automationJobs", "automationSchedules", "automationRules", "automationAlerts", "automationReports"];
    const kinds = { forecast: "Pronóstico", indicators: "Indicadores", evaluation: "Evaluación", report: "Reporte" };
    const states = { pending: "pendiente", running: "ejecutando", completed: "completado", failed: "fallido",
        canceled: "cancelado" };
    const signals = { sales_change: "Ventas previstas", demand_change: "Demanda prevista",
        metric_degradation: "Deterioro de WAPE", stale_data: "Datos desactualizados", run_failure: "Fallo de ejecución" };
    let scope = null;
    let timer = null;

    function add(host, tag, value) {
        const item = document.createElement(tag);
        item.textContent = value;
        host.appendChild(item);
        return item;
    }

    function reset() {
        scope = null;
        if (timer !== null) clearTimeout(timer);
        timer = null;
        for (const id of groups) node(id).replaceChildren();
        for (const id of ["automationMessage", "automationSummary"]) node(id).textContent = "";
        node("automationJobForm").reset();
        node("automationRuleForm").reset();
    }

    function open(url, version, permissions) {
        reset();
        scope = { url, version, manage: permissions.includes("dataset_manage"),
            run: permissions.includes("forecast_run"), request: 0 };
        node("automationJobForm").hidden = !scope.run;
        node("automationSchedule").hidden = !scope.manage;
        node("automationRuleForm").hidden = !scope.manage;
        refresh();
    }

    async function command(method, suffix, payload, headers) {
        const context = scope;
        if (!context) return;
        const serialized = JSON.stringify(payload);
        if (suffix === "/jobs") {
            if (context.requestBody !== serialized) {
                context.requestBody = serialized;
                context.key = globalThis.crypto?.randomUUID?.();
            }
            if (context.key) headers = { "Idempotency-Key": context.key };
        }
        try {
            await api(method, context.url + suffix, payload, headers);
            if (suffix === "/jobs") context.requestBody = null;
            if (scope !== context) return;
            node("automationMessage").textContent = "Operación registrada.";
            await refresh();
        } catch (error) {
            if (scope === context) node("automationMessage").textContent = error.message;
        }
    }

    function button(row, text, action) {
        const el = add(row, "button", text);
        el.type = "button";
        el.className = "secondary-button";
        el.addEventListener("click", action);
    }

    function listing(id, entries, render) {
        const host = node(id);
        host.replaceChildren();
        if (!entries.length) { add(host, "p", "Sin registros."); return; }
        const list = add(host, "ul", "");
        entries.forEach(value => render(add(list, "li", ""), value));
    }

    function render(context, data) {
        const indicators = data.latest.indicators?.result;
        const evaluation = data.latest.evaluation?.result;
        const forecast = data.latest.forecast?.result;
        const info = [];
        if (indicators) info.push("Indicadores v" + indicators.origin.version + ": " +
            indicators.metrics.records + " registros, " + indicators.metrics.sales + " " + indicators.origin.currency +
            " en ventas, " + indicators.metrics.units + " unidades y " + indicators.metrics.products +
            " productos. Último dato: " + indicators.origin.last_source_date + ". Filas válidas: " +
            indicators.origin.quality.valid_rows + ".");
        if (forecast) info.push("Último pronóstico: #" + forecast.run_id + " (v" + forecast.origin.version + ").");
        if (evaluation) info.push(evaluation.outcome === "evaluated"
            ? "Última evaluación #" + evaluation.run_id + ": MAE " + evaluation.metrics.mae.toFixed(2) +
                ", WAPE " + (evaluation.metrics.wape === null ? "N/D" : (evaluation.metrics.wape * 100).toFixed(1) + " %") +
                " y cobertura " + (evaluation.metrics.coverage * 100).toFixed(1) + " %."
            : "Evaluación pendiente de observaciones para #" + evaluation.run_id + ".");
        node("automationSummary").textContent = info.join(" ");
        listing("automationJobs", data.jobs, (row, job) => {
            add(row, "span", "#" + job.id + " · " + kinds[job.kind] + " · v" + job.parameters.version +
                " · " + states[job.state] + " · intento " + job.attempts + "/" + job.max_attempts +
                (job.error_code ? " · " + job.error_code : "") + " ");
            if (job.can_cancel && ["pending", "running"].includes(job.state)) {
                button(row, "Cancelar", () => command("DELETE", "/jobs/" + job.id));
            }
        });
        listing("automationSchedules", data.schedules, (row, schedule) => {
            add(row, "span", "#" + schedule.id + " · " + kinds[schedule.kind] + " · v" + schedule.parameters.version +
                (schedule.cadence === "daily" ? " · diaria" : " · semanal") +
                (schedule.enabled ? " · próxima " + new Date(schedule.next_at * 1000).toISOString() : " · pausada") + " ");
            if (context.manage) button(row, schedule.enabled ? "Pausar" : "Reactivar",
                () => command("PATCH", "/schedules/" + schedule.id, { enabled: !schedule.enabled }));
        });
        listing("automationRules", data.rules, (row, rule) => {
            add(row, "span", signals[rule.kind] + " · " + (rule.comparison === "above" ? "≥ " : "≤ ") +
                rule.threshold + " · " + rule.severity + (rule.enabled ? "" : " · desactivada") + " ");
            if (context.manage) button(row, rule.enabled ? "Desactivar" : "Activar",
                () => command("PATCH", "/rules/" + rule.id, { enabled: !rule.enabled }));
        });
        listing("automationAlerts", data.alerts, (row, alert) => {
            const evidence = alert.evidence;
            add(row, "span", signals[evidence.kind] + " · " + evidence.severity + " · " +
                evidence.value.toFixed(2) + " · v" + evidence.version + " · " + alert.created_at.slice(0, 10) + " ");
            if (context.manage) button(row, "Resolver", () => command("PATCH", "/alerts/" + alert.id, {}));
        });
        listing("automationReports", data.reports, (row, report) => {
            add(row, "span", "#" + report.id + " · v" + report.parameters.version + " ");
            for (const format of ["csv", "pdf"]) {
                add(row, "a", format.toUpperCase() + " ").href = context.url + "/reports/" + report.id + "/" + format;
            }
        });
    }

    async function refresh() {
        const context = scope;
        if (!context) return;
        if (timer !== null) clearTimeout(timer);
        const request = ++context.request;
        try {
            const result = await api("GET", context.url);
            if (scope !== context || request !== context.request) return;
            render(context, result);
            if (result.jobs.some(job => ["pending", "running"].includes(job.state))) timer = setTimeout(refresh, 6000);
        } catch (error) {
            if (scope === context && request === context.request) node("automationMessage").textContent = error.message;
        }
    }

    node("automationRefresh").addEventListener("click", refresh);
    node("automationJobForm").addEventListener("submit", event => {
        event.preventDefault();
        if (!scope) return;
        const form = Object.fromEntries(new FormData(event.target));
        const kind = form.kind;
        const payload = kind === "forecast" ? readForecast(scope.version) : { version: scope.version };
        payload.kind = kind;
        if (kind === "evaluation" || kind === "report" && form.run_id) payload.run_id = Number(form.run_id);
        if (kind === "evaluation") payload.zero_dates = form.zero_dates.split(",").map(day => day.trim()).filter(Boolean);
        if (event.submitter?.value === "schedule") {
            payload.cadence = form.cadence;
            payload.hour_utc = Number(form.hour);
            payload.weekday = Number(form.weekday);
            command("POST", "/schedules", payload);
        } else {
            command("POST", "/jobs", payload);
        }
    });
    node("automationRuleForm").addEventListener("submit", event => {
        event.preventDefault();
        const payload = Object.fromEntries(new FormData(event.target));
        payload.threshold = Number(payload.threshold);
        command("POST", "/rules", payload);
    });
    return { open, reset, refresh };
};
