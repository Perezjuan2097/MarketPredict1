/* Private forecast panel fed exclusively by the organization-scoped API. */
window.createEnterpriseForecastPanel = function (api) {
    const node = id => document.getElementById(id);
    const panel = node("enterpriseForecasts");
    const form = node("forecastForm");
    const history = node("forecastHistory");
    const info = node("forecastMessage");
    const fmt = new Intl.NumberFormat("es", { maximumFractionDigits: 4 });
    let scope = null;
    let chart = null;
    let readRequest = 0;

    function add(host, tag, value) {
        const el = document.createElement(tag);
        el.textContent = value;
        host.appendChild(el);
        return el;
    }

    function table(host, headings, rows) {
        host.replaceChildren();
        const table = add(host, "table", "");
        table.className = "table";
        const head = add(add(table, "thead", ""), "tr", "");
        headings.forEach(value => { add(head, "th", value).scope = "col"; });
        const body = add(table, "tbody", "");
        for (const cells of rows) {
            const row = add(body, "tr", "");
            cells.forEach(value => add(row, "td", value));
        }
    }

    function wipeResult() {
        node("forecastResult").hidden = true;
        node("forecastQuality").textContent = "";
        node("forecastPoints").replaceChildren();
        node("forecastModels").replaceChildren();
        chart?.destroy();
        chart = null;
    }

    function reset() {
        scope = null;
        readRequest++;
        panel.hidden = true;
        info.textContent = "";
        history.replaceChildren();
        form.reset();
        node("forecastGroup").hidden = true;
        form.elements.group.required = false;
        wipeResult();
    }

    function addRun(run) {
        if (Array.from(history.options).some(option => option.value === String(run.id))) return;
        add(history, "option", "#" + run.id + " · v" + run.parameters.version + " · " +
            (run.parameters.target === "revenue" ? "Ingresos" : "Unidades") + " · " + run.state).value = run.id;
    }

    async function loadHistory(context) {
        const { runs } = await api("GET", context.url + "?after=" + context.after);
        if (scope !== context) return;
        runs.forEach(addRun);
        context.after = runs.at(-1)?.id || context.after;
        node("forecastMore").hidden = runs.length < 50;
    }

    async function open(url, version, permissions) {
        reset();
        const context = scope = { url, version, after: 0, running: false };
        node("forecastVersion").textContent =
            "Se usará la versión " + version + ". Selecciona otro dataset o versión en Datos empresariales.";
        form.hidden = !permissions.includes("forecast_run");
        panel.hidden = false;
        node("forecastMore").hidden = true;
        add(history, "option", "Selecciona una ejecución").value = "";
        try { await loadHistory(context); }
        catch (error) { if (scope === context) info.textContent = error.message; }
    }

    const pct = value => value == null ? "N/D" : fmt.format(value * 100) + " %";
    const num = value => value == null ? "N/D" : fmt.format(value);

    function draw(result) {
        if (typeof Chart !== "function") return;
        const historical = result.history.map(point => point.value);
        const pad = values => Array(historical.length).fill(null).concat(values);
        const range = result.forecast.map(point => point.date);
        const traces = [
            { label: "Observado", data: historical.concat(Array(range.length).fill(null)), borderColor: "#2563eb" },
            { label: "Pronóstico", data: pad(result.forecast.map(point => point.value)), borderColor: "#047857" },
            { label: "Límite superior", data: pad(result.forecast.map(point => point.high)), borderColor: "#64748b",
                fill: "+1", backgroundColor: "rgba(100, 116, 139, 0.18)" },
            { label: "Límite inferior", data: pad(result.forecast.map(point => point.low)), borderColor: "#64748b" },
        ];
        if (result.scenario) traces.push({
            label: "Escenario hipotético", data: pad(result.scenario.points.map(point => point.value)),
            borderColor: "#92400e", borderDash: [5, 4],
        });
        chart = new Chart(node("forecastChart"), {
            type: "line",
            data: { labels: result.history.map(point => point.date).concat(range), datasets: traces },
            options: { animation: false, elements: { point: { radius: 0 } } },
        });
    }

    function render(run) {
        wipeResult();
        info.textContent = run.state === "ready" ? "" : "Estado de la ejecución: " + run.state + ".";
        if (!run.result) return;
        const result = run.result;
        const interval = result.interval;
        const provenance = run.provenance;
        const evaluation = result.beats_baseline_on_final
            ? "El modelo redujo el MAE frente a la referencia en el bloque final."
            : "El bloque final no aporta evidencia de mejora sobre la referencia.";
        const scenario = result.scenario
            ? " Escenario hipotético: ajuste del " + num(result.scenario.pct) + " % sobre el pronóstico, sin probabilidad asignada."
            : "";
        node("forecastQuality").textContent = evaluation + " Comparación histórica. Modelo: " + result.selected_model
            + ". Referencia: " + result.baseline + ". Cobertura final: " + pct(interval.coverage_final)
            + " de " + interval.final_points + " puntos (nivel nominal " + pct(interval.nominal_level) + "). "
            + provenance.zero_days_declared + " días de cero declarados; " + provenance.negative_source_rows
            + " filas con valores negativos; " + provenance.outlier_days + " días atípicos. "
            + (provenance.observations_excluded_after_cutoff ? "Se omitieron fechas posteriores al corte. " : "")
            + "El intervalo estima incertidumbre histórica y no garantiza resultados." + scenario;
        const unit = run.parameters.target === "revenue" ? provenance.currency : "unidades";
        const headings = ["Fecha", "Pronóstico (" + unit + ")", "Límite inferior", "Límite superior"];
        if (result.scenario) headings.push("Escenario hipotético");
        table(node("forecastPoints"), headings, result.forecast.map((point, i) => {
            const values = [point.date, num(point.value), num(point.low), num(point.high)];
            if (result.scenario) values.push(num(result.scenario.points[i].value));
            return values;
        }));
        table(node("forecastModels"), ["Modelo", "MAE selección", "MAE final", "RMSE final", "WAPE final", "Sesgo final"],
              Object.entries(result.models).map(([model, score]) => [
                  model, num(score.selection.mae), num(score.final?.mae), num(score.final?.rmse),
                  pct(score.final?.wape), num(score.final?.bias),
              ]));
        node("forecastResult").hidden = false;
        draw(result);
    }

    form.elements.group_by.addEventListener("change", () => {
        const allDates = form.elements.group_by.value === "date";
        node("forecastGroup").hidden = allDates;
        form.elements.group.required = !allDates;
    });
    form.addEventListener("submit", async event => {
        event.preventDefault();
        const context = scope;
        if (!context || context.running) return;
        const body = Object.fromEntries(new FormData(form));
        body.version = context.version;
        body.horizon = Number(body.horizon);
        body.zero_dates = body.zero_dates.split(",").map(value => value.trim()).filter(Boolean);
        body.scenario_pct = body.scenario_pct === "" ? null : Number(body.scenario_pct);
        context.running = true;
        info.textContent = "Evaluando el historial y calculando el pronóstico…";
        try {
            const { run } = await api("POST", context.url, body);
            if (scope !== context) return;
            readRequest++;
            addRun(run);
            history.value = run.id;
            render(run);
        } catch (error) {
            if (scope === context) info.textContent = error.message;
        } finally {
            context.running = false;
        }
    });
    history.addEventListener("change", async () => {
        const context = scope;
        const request = ++readRequest;
        wipeResult();
        if (!context || !history.value) return;
        try {
            const { run } = await api("GET", context.url + "/" + history.value);
            if (scope === context && request === readRequest) render(run);
        } catch (error) {
            if (scope === context && request === readRequest) info.textContent = error.message;
        }
    });
    node("forecastMore").addEventListener("click", async () => {
        const context = scope;
        if (!context) return;
        try { await loadHistory(context); }
        catch (error) { if (scope === context) info.textContent = error.message; }
    });
    return { open, reset };
};
