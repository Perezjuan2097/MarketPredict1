(() => {
    const get = id => document.getElementById(id);
    const panel = get("commercialPanel");
    if (!panel) return;
    const base = "/admin/api/commercial";
    const writable = panel.dataset.writable === "true";
    const captions = { users: "Usuarios", datasets: "Datasets", storage_bytes: "Bytes lógicos",
        imports: "Importaciones del mes", forecasts: "Pronósticos del mes", automations: "Automatizaciones",
        retention_days: "Retención operativa en días" };
    const status = get("commercialMessage");
    let after = 0;
    let active = 0;

    function add(host, tag, text) {
        const node = document.createElement(tag);
        node.textContent = text;
        host.appendChild(node);
        return node;
    }

    function table(host, headings, records) {
        host.replaceChildren();
        const result = add(host, "table", "");
        result.className = "table";
        const header = add(add(result, "thead", ""), "tr", "");
        for (const heading of headings) add(header, "th", heading).scope = "col";
        const body = add(result, "tbody", "");
        for (const cells of records) {
            const row = add(body, "tr", "");
            for (const cell of cells) {
                const td = add(row, "td", typeof cell === "object" ? "" : cell);
                if (cell && typeof cell === "object") td.appendChild(cell);
            }
        }
    }

    async function request(method, path, body) {
        const response = await fetch(base + path, {
            method, headers: { "Content-Type": "application/json", "X-CSRF-Token": panel.dataset.csrf },
            body: body === undefined ? undefined : JSON.stringify(body),
        });
        const data = response.status === 204 ? null : await response.json().catch(() => null);
        if (!response.ok) throw new Error(data?.error || "No se pudo completar la operación.");
        return data;
    }

    async function change(method, path, body) {
        try {
            await request(method, path, body);
            await load();
            status.textContent = "Cambio registrado.";
        } catch (error) { status.textContent = error.message; }
    }

    function assignment(organization, plans) {
        if (!writable) return organization.plan;
        const form = document.createElement("form");
        const select = add(form, "select", "");
        select.setAttribute("aria-label", "Plan de " + organization.slug);
        for (const plan of Object.keys(plans)) {
            const option = add(select, "option", plan);
            option.selected = organization.plan === plan;
        }
        add(form, "button", "Asignar").type = "submit";
        form.addEventListener("submit", event => {
            event.preventDefault();
            change("PATCH", "/organizations/" + organization.id, { plan: select.value });
        });
        return form;
    }

    function display(data) {
        get("commercialServices").textContent = "Web " + (data.web_ready ? "lista" : "sin readiness") +
            " · cola pendiente " + data.queue.pending + " · fallos " + data.queue.failed +
            " · más antiguo " + (data.queue.oldest_due_seconds ?? "—") + " s · workers " +
            (data.workers.map(worker => worker.state + " (" + worker.age_seconds + " s)").join(", ") || "sin heartbeat");
        table(get("commercialOrganizations"), ["ID", "Organización", "Estado", "Plan", "Uso / límite"],
              data.organizations.map(org => [org.id, org.name + " · " + org.slug, org.status,
                  assignment(org, data.plans), Object.keys(org.usage).map(k => captions[k] + " " +
                    org.usage[k] + "/" + org.limits[k]).join(" · ")]));
        table(get("commercialJobs"), ["ID", "Organización", "Tipo", "Estado", "Duración (s)", "Error", "Correlación"],
              data.jobs.map(job => [job.id, job.organization_id, job.kind, job.state, job.duration_seconds ?? "—",
                  job.error_code || "—", job.request_id || "—"]));
        const events = get("commercialEvents");
        events.replaceChildren();
        for (const event of data.events) add(events, "li", [event.date, event.kind, event.organization_id,
            event.actor_id, event.reference].filter(value => value != null).join(" · "));
        const plans = get("commercialPlans");
        plans.replaceChildren();
        for (const [name, limits] of Object.entries(data.plans)) {
            add(plans, "h3", name);
            const form = add(plans, "form", "");
            form.className = "account-form";
            for (const [key, value] of Object.entries(limits)) {
                const label = add(form, "label", captions[key]);
                const input = add(label, "input", "");
                input.name = key; input.type = "number"; input.min = key === "retention_days" ? "1" : "0";
                input.value = value; input.readOnly = !writable; input.required = true;
            }
            if (writable) {
                add(form, "button", "Guardar límites").type = "submit";
                form.addEventListener("submit", event => {
                    event.preventDefault();
                    change("PUT", "/plans/" + name, Object.fromEntries(Array.from(new FormData(form),
                        ([key, value]) => [key, Number(value)])));
                });
            }
        }
        const next = get("commercialNext");
        next.hidden = data.organizations.length < 30;
        next.onclick = () => { after = data.organizations.at(-1).id; load(); };
        get("commercialFirst").hidden = after === 0;
    }

    async function load() {
        const sequence = ++active;
        try {
            const data = await request("GET", "?limit=30&after=" + after);
            if (active === sequence) display(data);
        } catch (error) {
            if (active === sequence) status.textContent = error.message;
        }
    }

    get("commercialRefresh").addEventListener("click", load);
    get("commercialFirst").addEventListener("click", () => { after = 0; load(); });
    load();
})();
