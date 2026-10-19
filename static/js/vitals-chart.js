/**
 * Vitals trend chart. Mark up with the vitals_chart() macro:
 *   <div class="vitals-chart" data-url="/api/vitals">…tabs + canvas…</div>
 * Needs Chart.js loaded first.
 */
(() => {
    const METRICS = {
        bp: { label: "Blood pressure (mmHg)", series: [["Systolic", "systolic", "#2563EB"], ["Diastolic", "diastolic", "#FF6B81"]] },
        weight: { label: "Weight (kg)", series: [["Weight", "weight", "#0EA5E9"]] },
        sugar: { label: "Blood sugar (mg/dL)", series: [["Blood sugar", "sugar", "#d97706"]] },
    };

    window.addEventListener("load", async () => {
        if (!window.Chart) return;
        for (const box of document.querySelectorAll(".vitals-chart")) {
            const canvas = box.querySelector("canvas");
            let data;
            try {
                const r = await fetch(box.dataset.url);
                if (!r.ok) continue;
                data = await r.json();
            } catch (e) { continue; }

            if (!data.labels.length) {
                box.querySelector(".chart-container").classList.add("hidden");
                box.querySelector(".chart-tabs").classList.add("hidden");
                box.querySelector(".vitals-empty").classList.remove("hidden");
                continue;
            }

            let chart;
            const draw = (key) => {
                const m = METRICS[key];
                const datasets = m.series.map(([name, field, color]) => ({
                    label: name, data: data[field], borderColor: color, backgroundColor: color,
                    tension: 0.25, spanGaps: true, pointRadius: 4,
                }));
                if (chart) chart.destroy();
                chart = new Chart(canvas, {
                    type: "line",
                    data: { labels: data.labels, datasets },
                    options: {
                        maintainAspectRatio: false,
                        plugins: { legend: { display: datasets.length > 1 }, title: { display: true, text: m.label } },
                        scales: { y: { beginAtZero: false } },
                    },
                });
            };

            // open on the first metric that actually has data
            const first = Object.keys(METRICS).find(
                (k) => METRICS[k].series.some(([, f]) => data[f].some((v) => v !== null))
            ) || "bp";
            box.querySelectorAll("[data-metric]").forEach((b) => {
                b.classList.toggle("active", b.dataset.metric === first);
                b.addEventListener("click", () => {
                    box.querySelectorAll("[data-metric]").forEach((x) => x.classList.toggle("active", x === b));
                    draw(b.dataset.metric);
                });
            });
            draw(first);
        }
    });
})();
