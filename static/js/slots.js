/**
 * Slot picker shared by the booking and reschedule pages.
 *
 * Expects #doctor_id (select or hidden input), #date, #time (hidden), #slotGrid, #slotStatus.
 * Optional: #doctorMeta plus window.DOCTOR_INFO = {id: {q, fee, days}}.
 */
(() => {
    const doctor = document.getElementById("doctor_id");
    const date = document.getElementById("date");
    const time = document.getElementById("time");
    const grid = document.getElementById("slotGrid");
    const status = document.getElementById("slotStatus");
    if (!doctor || !date || !time || !grid || !status) return;

    const fmt = (hhmm) => {
        const [h, m] = hhmm.split(":").map(Number);
        return `${((h + 11) % 12) + 1}:${String(m).padStart(2, "0")} ${h < 12 ? "AM" : "PM"}`;
    };

    let requestId = 0;   // ignore responses that arrive after a newer request

    async function load() {
        const mine = ++requestId;
        grid.innerHTML = "";
        const chosen = time.value;
        if (!doctor.value || !date.value) {
            status.textContent = "Choose a doctor and date to see slots.";
            return;
        }
        status.textContent = "Loading slots…";
        try {
            const r = await fetch(`/api/doctors/${doctor.value}/slots?date=${date.value}`);
            const data = await r.json();
            if (mine !== requestId) return;
            if (!r.ok) { status.textContent = data.error || "Couldn't load slots."; return; }
            if (!data.works) {
                status.textContent = `This doctor works ${data.days.join(", ")}. Pick another day.`;
                time.value = "";
                return;
            }
            const free = data.slots.filter((s) => s.available).length;
            status.textContent = free
                ? `${free} slot${free === 1 ? "" : "s"} available`
                : "No free slots that day — try another.";
            data.slots.forEach((s) => {
                const b = document.createElement("button");
                b.type = "button";
                b.className = "slot-btn" + (s.available ? "" : " slot-booked")
                    + (s.time === chosen && s.available ? " slot-selected" : "");
                b.textContent = fmt(s.time);
                b.disabled = !s.available;
                b.title = s.blocked ? "Doctor unavailable" : (s.available ? "" : "Booked");
                b.setAttribute("role", "option");
                b.addEventListener("click", () => {
                    time.value = s.time;
                    grid.querySelectorAll(".slot-btn").forEach((x) => x.classList.remove("slot-selected"));
                    b.classList.add("slot-selected");
                });
                grid.appendChild(b);
            });
            if (!data.slots.some((s) => s.available && s.time === chosen)) time.value = "";
        } catch (e) {
            if (mine === requestId) status.textContent = "Network error. Please try again.";
        }
    }

    const showMeta = () => {
        const el = document.getElementById("doctorMeta");
        const d = (window.DOCTOR_INFO || {})[doctor.value];
        if (el) el.textContent = d ? `${d.q} · ₹${d.fee} · works ${d.days}` : "";
    };

    doctor.addEventListener("change", () => { time.value = ""; showMeta(); load(); });
    date.addEventListener("change", () => { time.value = ""; load(); });
    showMeta();
    if (date.value) load();
})();
