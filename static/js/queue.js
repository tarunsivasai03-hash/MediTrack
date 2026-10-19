/**
 * Live queue board. Polls /api/queue every few seconds and redraws the table.
 *
 *   <div id="queue" data-url="/api/queue" data-refresh="10">
 *     <table><tbody id="queueBody"></tbody></table> ... #queueSummary, #queueUpdated, #queueEmpty
 *   </div>
 *   optional: <select id="queueDoctor"> to filter by doctor.
 *
 * All text is inserted with textContent, never innerHTML, so patient names can't inject markup.
 */
(() => {
    const root = document.getElementById("queue");
    if (!root) return;
    const body = document.getElementById("queueBody");
    const summary = document.getElementById("queueSummary");
    const updated = document.getElementById("queueUpdated");
    const empty = document.getElementById("queueEmpty");
    const filter = document.getElementById("queueDoctor");
    const interval = (parseInt(root.dataset.refresh, 10) || 10) * 1000;

    const LABELS = { scheduled: "Scheduled", waiting: "Waiting", in_consult: "In consultation", done: "Done", no_show: "No-show", cancelled: "Cancelled" };

    const el = (tag, cls, text) => {
        const n = document.createElement(tag);
        if (cls) n.className = cls;
        if (text !== undefined) n.textContent = text;
        return n;
    };

    function cell(label, ...children) {
        const td = el("td");
        td.dataset.label = label;
        children.forEach((c) => td.append(c));
        return td;
    }

    async function act(a) {
        if (a.confirm && !window.confirm(a.confirm)) return;
        const form = new URLSearchParams(a.data || {});
        await fetch(a.url, {
            method: "POST", body: form,
            headers: { "X-CSRFToken": getCSRFToken(), "Content-Type": "application/x-www-form-urlencoded" },
        });
        load();
    }

    function render(data) {
        body.replaceChildren();
        summary.replaceChildren();
        Object.keys(LABELS).forEach((k) => {
            if (data.counts[k]) summary.append(el("span", "q-pill", `${LABELS[k]}: ${data.counts[k]}`));
        });
        empty.classList.toggle("hidden", data.rows.length > 0);

        data.rows.forEach((r) => {
            const tr = el("tr", `queue-row state-${r.state}`);
            const when = el("span", null, r.time);
            tr.append(cell("Time", when, ...(r.walk_in ? [el("span", "walk-tag", "WALK-IN")] : [])));
            tr.append(cell("Patient", el("strong", null, r.patient),
                r.reason ? el("small", "text-muted", " · " + r.reason) : document.createTextNode("")));
            tr.append(cell("Doctor", document.createTextNode(r.doctor)));

            const status = el("span", `badge badge-${r.state}`, LABELS[r.state] || r.state);
            const extra = r.state === "waiting"
                ? el("small", "text-muted", ` #${r.position} · ${r.wait_minutes} min`) : document.createTextNode("");
            tr.append(cell("Status", status, extra));

            const actions = el("td", "row-actions");
            (r.actions || []).forEach((a) => {
                if (a.method === "GET") {
                    const link = el("a", `btn btn-sm ${a.style || "btn-secondary"}`, a.label);
                    link.href = a.url;
                    actions.append(link);
                } else {
                    const b = el("button", `btn btn-sm ${a.style || "btn-secondary"}`, a.label);
                    b.type = "button";
                    b.addEventListener("click", () => act(a));
                    actions.append(b);
                }
            });
            tr.append(actions);
            body.append(tr);
        });
        updated.textContent = data.updated;
    }

    async function load() {
        try {
            const url = new URL(root.dataset.url, location.origin);
            if (filter && filter.value) url.searchParams.set("doctor", filter.value);
            const r = await fetch(url);
            if (r.ok) render(await r.json());
        } catch (e) { /* keep the last good view while offline */ }
    }

    filter?.addEventListener("change", load);
    document.addEventListener("visibilitychange", () => { if (!document.hidden) load(); });
    load();
    setInterval(() => { if (!document.hidden) load(); }, interval);
})();
