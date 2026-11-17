/* Dataset panel. The authenticated API enforces all organization permissions. */
window.createDatasetPanel = function (api) {
    const byId = id => document.getElementById(id);
    const panel = byId("enterpriseDatasets");
    const forecasts = window.createEnterpriseForecastPanel?.(api);
    const fields = {
        date: "Fecha", product: "Producto", quantity: "Cantidad",
        price: "Precio", revenue: "Ingresos", category: "Categoría", transaction_id: "ID transacción",
    };
    const pageSize = 50;
    let scope = null;
    let selection = null;
    let preview = null;

    function add(parent, tag, text) {
        const item = document.createElement(tag);
        item.textContent = text;
        parent.appendChild(item);
        return item;
    }

    function table(host, columns, rows) {
        host.replaceChildren();
        if (!rows.length) return;
        const el = add(host, "table", "");
        el.className = "table";
        const header = add(add(el, "thead", ""), "tr", "");
        for (const title of columns) add(header, "th", title).scope = "col";
        const body = add(el, "tbody", "");
        for (const values of rows) {
            const row = add(body, "tr", "");
            for (const value of values) add(row, "td", value ?? "—");
        }
    }

    function forgetPreview() {
        preview = null;
        byId("datasetConfirm").hidden = true;
        byId("datasetPreview").replaceChildren();
    }

    function reset() {
        forecasts?.reset();
        scope = selection = null;
        panel.hidden = true;
        byId("datasetDetail").hidden = true;
        byId("datasetMessage").textContent = "";
        byId("datasetQuality").textContent = "";
        for (const id of ["datasetSelect", "datasetVersion", "datasetMetrics", "datasetSeries", "datasetRows", "datasetFields"]) {
            byId(id).replaceChildren();
        }
        byId("datasetCreate").reset();
        byId("datasetRename").reset();
        byId("datasetFile").value = "";
        byId("datasetMapping").hidden = true;
        forgetPreview();
    }

    async function loadList(context, after = 0, created = null) {
        const { datasets } = await api("GET", context.url + "?after=" + after);
        if (scope !== context) return;
        const select = byId("datasetSelect");
        if (!after) {
            select.replaceChildren();
            add(select, "option", "Selecciona un dataset").value = "";
        }
        for (const dataset of datasets) {
            if (!Array.from(select.options).some(option => option.value === String(dataset.id))) {
                add(select, "option", dataset.name).value = dataset.id;
            }
        }
        if (created && !Array.from(select.options).some(option => option.value === String(created.id))) {
            add(select, "option", created.name).value = created.id;
        }
        context.after = datasets.at(-1)?.id || after;
        byId("datasetMore").hidden = datasets.length < pageSize;
        if (created) {
            select.value = created.id;
            await selectDataset();
        }
    }

    async function open(url, permissions) {
        reset();
        if (!permissions.includes("dataset_read")) return;
        const context = scope = { url, permissions, after: 0 };
        byId("datasetCreate").hidden = !permissions.includes("dataset_manage");
        panel.hidden = false;
        try {
            await loadList(context);
        } catch (error) {
            if (scope === context) byId("datasetMessage").textContent = error.message;
        }
    }

    async function loadVersions(item, before = null) {
        const { versions } = await api("GET", item.url + "/versions" + (before ? "?before=" + before : ""));
        if (selection !== item) return;
        const select = byId("datasetVersion");
        if (!before) {
            item.versions = [];
            select.replaceChildren();
            add(select, "option", "Última versión").value = "";
        }
        item.versions.push(...versions);
        for (const version of versions) {
            add(select, "option", "v" + version.version + " · " + version.uploaded_at.slice(0, 10)
                + " · " + version.format.toUpperCase()).value = version.version;
        }
        byId("datasetMoreVersions").hidden = versions.length < pageSize;
    }

    async function loadSummary(item) {
        const selectedVersion = byId("datasetVersion").value;
        const result = await api("GET", item.url + (selectedVersion ? "?version=" + selectedVersion : ""));
        if (selection !== item || selectedVersion !== byId("datasetVersion").value) return;
        byId("datasetRename").elements.name.value = result.dataset.name;
        const metrics = byId("datasetMetrics");
        metrics.replaceChildren();
        const version = item.versions.find(entry => entry.version === result.version);
        const quality = version?.quality;
        const missing = quality ? Object.values(quality.missing_cells).reduce((a, b) => a + b, 0) : 0;
        byId("datasetQuality").textContent = quality
            ? "Versión " + version.version + " · " + version.status + " · " + quality.valid_rows
                + " filas válidas · " + missing + " celdas vacías · "
                + quality.revenue_discrepancies + " diferencias entre ingresos informados y calculados"
            : "Todavía no hay filas importadas.";
        if (result.metrics) {
            const values = [
                ["Registros", result.metrics.records], ["Ventas (" + result.dataset.currency + ")", result.metrics.sales],
                ["Unidades", result.metrics.units], ["Productos", result.metrics.products],
            ];
            for (const [label, value] of values) {
                const article = add(metrics, "article", "");
                article.className = "card";
                add(article, "small", label);
                add(article, "strong", value);
            }
        }
        table(byId("datasetSeries"), ["Fecha", "Ventas (" + result.dataset.currency + ")", "Unidades"],
              result.series.map(day => [day.date, day.sales, day.units]));
        const grid = item.grid = { version: result.version, after: 0 };
        byId("datasetRows").replaceChildren();
        byId("datasetMoreRows").hidden = true;
        if (grid.version !== null) {
            forecasts?.open(item.url + "/forecasts", grid.version, scope.permissions);
            await loadRows(item, grid);
        } else {
            forecasts?.reset();
        }
    }

    async function loadRows(item, grid) {
        const { rows } = await api("GET", item.url + "/rows?version=" + grid.version + "&after=" + grid.after);
        if (selection !== item || grid !== item.grid) return;
        const host = byId("datasetRows");
        if (rows.length) {
            if (!grid.after) {
                table(host, Object.values(fields),
                      rows.map(row => Object.keys(fields).map(key => row[key])));
            } else {
                const body = host.querySelector("tbody");
                for (const row of rows.filter(row => row.row_number > grid.after)) {
                    const entry = add(body, "tr", "");
                    for (const key of Object.keys(fields)) add(entry, "td", row[key] ?? "—");
                }
            }
            grid.after = Math.max(grid.after, rows.at(-1).row_number);
        }
        byId("datasetMoreRows").hidden = rows.length < pageSize;
    }

    async function selectDataset() {
        forecasts?.reset();
        const id = byId("datasetSelect").value;
        selection = null;
        forgetPreview();
        byId("datasetFile").value = "";
        byId("datasetMapping").hidden = true;
        byId("datasetDetail").hidden = true;
        if (!scope || !id) return;
        const item = selection = { url: scope.url + "/" + id, versions: [] };
        byId("datasetRename").hidden = !scope.permissions.includes("dataset_manage");
        byId("datasetArchive").hidden = !scope.permissions.includes("dataset_delete");
        byId("datasetImport").hidden = !scope.permissions.includes("dataset_import");
        await loadVersions(item);
        if (selection !== item) return;
        await loadSummary(item);
        if (selection === item) byId("datasetDetail").hidden = false;
    }

    function fileBody(file) {
        if (!file) throw new Error("Selecciona un archivo.");
        const body = new FormData();
        body.set("file", file);
        return body;
    }

    function bind(id, event, action) {
        byId(id).addEventListener(event, async e => {
            e.preventDefault();
            const context = scope;
            const item = selection;
            if (!context) return;
            byId("datasetMessage").textContent = "";
            try {
                await action(context, item, e);
            } catch (error) {
                if (scope === context && selection === item) byId("datasetMessage").textContent = error.message;
            }
        });
    }

    bind("datasetMore", "click", context => loadList(context, context.after));
    bind("datasetSelect", "change", async context => {
        try {
            await selectDataset();
        } catch (error) {
            if (scope === context) byId("datasetMessage").textContent = error.message;
        }
    });
    bind("datasetVersion", "change", (_, item) => {
        forecasts?.reset();
        return loadSummary(item);
    });
    bind("datasetMoreRows", "click", (_, item) => loadRows(item, item.grid));
    bind("datasetMoreVersions", "click", (_, item) => loadVersions(item, item.versions.at(-1)?.version));
    bind("datasetCreate", "submit", async (context, _, event) => {
        const { dataset } = await api("POST", context.url, { name: event.target.elements.name.value });
        if (scope !== context) return;
        event.target.reset();
        await loadList(context, 0, dataset);
    });
    bind("datasetRename", "submit", async (context, item, event) => {
        const { dataset } = await api("PATCH", item.url, { name: event.target.elements.name.value });
        if (scope !== context || selection !== item) return;
        byId("datasetSelect").selectedOptions[0].textContent = dataset.name;
        byId("datasetMessage").textContent = "Nombre actualizado.";
    });
    bind("datasetArchive", "click", async (context, item) => {
        if (!window.confirm("¿Archivar este dataset? Sus versiones dejarán de estar visibles.")) return;
        await api("DELETE", item.url);
        if (scope !== context || selection !== item) return;
        byId("datasetDetail").hidden = true;
        selection = null;
        forecasts?.reset();
        forgetPreview();
        await loadList(context);
    });
    byId("datasetFile").addEventListener("change", () => {
        forgetPreview();
        byId("datasetMapping").hidden = true;
    });
    byId("datasetFields").addEventListener("change", forgetPreview);
    bind("datasetInspect", "click", async (_, item) => {
        forgetPreview();
        byId("datasetMapping").hidden = true;
        const file = byId("datasetFile").files[0];
        const data = await api("POST", item.url + "/preview", fileBody(file));
        if (selection !== item || byId("datasetFile").files[0] !== file) return;
        const host = byId("datasetFields");
        host.replaceChildren();
        for (const [key, label] of Object.entries(fields)) {
            const select = add(add(host, "label", label), "select", "");
            select.name = key;
            add(select, "option", "Sin asignar").value = "";
            for (const column of data.columns) {
                const option = add(select, "option", column);
                option.value = column;
                option.selected = [key, label.toLowerCase()].includes(column.toLowerCase());
            }
        }
        table(byId("datasetPreview"), data.columns, data.sample);
        byId("datasetMapping").hidden = false;
    });
    bind("datasetMapping", "submit", async (_, item, event) => {
        forgetPreview();
        const file = byId("datasetFile").files[0];
        const mapping = () => JSON.stringify(Object.fromEntries(
            Array.from(new FormData(event.target)).filter(([, value]) => value)));
        const selectedMapping = mapping();
        const body = fileBody(file);
        body.set("mapping", selectedMapping);
        const data = await api("POST", item.url + "/preview", body);
        if (selection !== item || byId("datasetFile").files[0] !== file || selectedMapping !== mapping()) return;
        table(byId("datasetPreview"), Object.values(fields),
              data.sample.map(row => Object.keys(fields).map(key => row[key])));
        const quality = data.quality;
        const errors = { missing: "faltante", duplicate: "duplicada", type_or_range: "tipo o rango inválido" };
        byId("datasetMessage").textContent = quality.valid_rows + " filas válidas. " + quality.invalid_rows
            + " inválidas. " + quality.duplicate_rows + " duplicadas. " + data.errors.map(error =>
                "Fila " + error.row + " (" + (fields[error.field] || "registro") + "): " + errors[error.code]).join(". ");
        if (data.preview_token) {
            preview = { file, token: data.preview_token };
            byId("datasetConfirm").hidden = false;
        }
    });
    bind("datasetConfirm", "click", async (_, item) => {
        if (!preview) return;
        const pending = preview;
        const body = fileBody(pending.file);
        body.set("preview_token", pending.token);
        const { imported } = await api("POST", item.url + "/imports", body);
        if (selection !== item || preview !== pending) return;
        forgetPreview();
        byId("datasetMapping").hidden = true;
        byId("datasetFile").value = "";
        byId("datasetMessage").textContent = "Versión " + imported.version + " disponible.";
        await loadVersions(item);
        if (selection === item) await loadSummary(item);
    });
    return { open, reset };
};
