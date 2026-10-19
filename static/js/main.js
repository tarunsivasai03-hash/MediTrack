/**
 * MediTrack — Main JavaScript
 *
 * Handles:
 *  - Sidebar toggle (mobile)
 *  - Flash message auto-dismiss
 *  - CSRF token helper for fetch() POST requests
 *  - Debounce utility
 */

document.addEventListener("DOMContentLoaded", () => {

    // ── Mobile drawer ────────────────────────────────────────────────────
    const sidebar  = document.getElementById("sidebar");
    const toggle   = document.getElementById("sidebarToggle");
    const closeBtn = document.getElementById("sidebarClose");
    const backdrop = document.getElementById("sidebarBackdrop");

    if (sidebar && toggle) {
        const setOpen = (open) => {
            sidebar.classList.toggle("open", open);
            backdrop && backdrop.classList.toggle("show", open);
            document.body.classList.toggle("no-scroll", open);
            toggle.setAttribute("aria-expanded", String(open));
            if (open) {
                const first = sidebar.querySelector(".nav-link");
                first && first.focus();
            } else {
                toggle.focus({ preventScroll: true });
            }
        };

        toggle.addEventListener("click", () => setOpen(!sidebar.classList.contains("open")));
        closeBtn && closeBtn.addEventListener("click", () => setOpen(false));
        backdrop && backdrop.addEventListener("click", () => setOpen(false));
        document.addEventListener("keydown", (e) => {
            if (e.key === "Escape" && sidebar.classList.contains("open")) setOpen(false);
        });
        // Reset when growing to desktop width
        window.matchMedia("(min-width: 901px)").addEventListener("change", (e) => {
            if (e.matches) {
                sidebar.classList.remove("open");
                backdrop && backdrop.classList.remove("show");
                document.body.classList.remove("no-scroll");
            }
        });
    }

    // ── Flash messages: dismiss button + auto-dismiss ────────────────────
    const dismiss = (flash) => {
        flash.classList.add("flash-out");
        setTimeout(() => flash.remove(), 300);
    };
    document.querySelectorAll(".flash").forEach((flash) => {
        flash.querySelector(".flash-close")?.addEventListener("click", () => dismiss(flash));
        setTimeout(() => dismiss(flash), 6000);
    });

    // ── Password show/hide ───────────────────────────────────────────────
    document.querySelectorAll("[data-toggle-password]").forEach((btn) => {
        const input = document.getElementById(btn.dataset.togglePassword);
        if (!input) return;
        btn.addEventListener("click", () => {
            const show = input.type === "password";
            input.type = show ? "text" : "password";
            btn.setAttribute("aria-pressed", String(show));
            btn.setAttribute("aria-label", show ? "Hide password" : "Show password");
            btn.classList.toggle("is-on", show);
        });
    });

    // ── Confirm destructive actions: <form data-confirm="…"> ─────────────
    document.querySelectorAll("form[data-confirm]").forEach((form) => {
        form.addEventListener("submit", (e) => {
            if (!window.confirm(form.dataset.confirm)) e.preventDefault();
        });
    });

    // ── Print button ─────────────────────────────────────────────────────
    document.querySelectorAll("[data-print]").forEach((b) => b.addEventListener("click", () => window.print()));

    // ── Prevent double-submit & show a busy state ────────────────────────
    document.querySelectorAll("form").forEach((form) => {
        form.addEventListener("submit", (e) => {
            if (e.defaultPrevented) return;
            const btn = form.querySelector('[type="submit"]');
            if (btn && form.checkValidity()) {
                setTimeout(() => { btn.disabled = true; btn.classList.add("is-loading"); }, 0);
            }
        });
    });
});

window.addEventListener("pageshow", (e) => {
    if (e.persisted) {
        document.querySelectorAll("button.is-loading").forEach((b) => { b.disabled = false; b.classList.remove("is-loading"); });
    }
});


// ── CSRF helper ──────────────────────────────────────────────────────────

/**
 * Read the CSRF token from the meta tag injected by Flask-WTF.
 * Falls back to reading it from a hidden input named csrf_token.
 */
function getCSRFToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    if (meta) return meta.getAttribute("content");

    const input = document.querySelector('input[name="csrf_token"]');
    if (input) return input.value;

    return "";
}

/**
 * Wrapper around fetch() that automatically includes:
 *  - Content-Type: application/json
 *  - X-CSRFToken header
 *
 * @param {string} url
 * @param {object} options - standard fetch options; body will be JSON-stringified
 * @returns {Promise<Response>}
 */
function fetchWithCSRF(url, options = {}) {
    const headers = {
        "Content-Type": "application/json",
        "X-CSRFToken": getCSRFToken(),
        ...(options.headers || {}),
    };

    return fetch(url, {
        ...options,
        headers,
        body: options.body ? JSON.stringify(options.body) : undefined,
    });
}


// ── Debounce utility ─────────────────────────────────────────────────────

/**
 * Returns a debounced version of `fn` that delays invocation
 * until `delay` ms have elapsed since the last call.
 *
 * @param {Function} fn
 * @param {number} delay - milliseconds
 * @returns {Function}
 */
function debounce(fn, delay = 300) {
    let timer;
    return function (...args) {
        clearTimeout(timer);
        timer = setTimeout(() => fn.apply(this, args), delay);
    };
}


// ── Theme (light / dark) ─────────────────────────────────────────────────
// The saved choice is applied before first paint by an inline script in base.html.
(() => {
    const root = document.documentElement;
    const apply = (dark) => {
        if (dark) root.setAttribute("data-theme", "dark"); else root.removeAttribute("data-theme");
        try { localStorage.setItem("hms-theme", dark ? "dark" : "light"); } catch (e) { /* private mode */ }
        window.dispatchEvent(new Event("hms-theme"));
    };
    document.addEventListener("click", (e) => {
        if (e.target.closest("[data-theme-toggle]")) apply(root.getAttribute("data-theme") !== "dark");
    });
})();

// Charts follow the theme: set Chart.js defaults from the page's own colours.
window.addEventListener("load", () => {
    if (!window.Chart) return;
    const css = getComputedStyle(document.documentElement);
    Chart.defaults.color = css.getPropertyValue("--text-secondary").trim() || "#3f5a43";
    Chart.defaults.borderColor = css.getPropertyValue("--border-color").trim() || "#d8e8d8";
});


// ── Installable app (PWA) ────────────────────────────────────────────────
(() => {
    if ("serviceWorker" in navigator && (location.protocol === "https:" || location.hostname === "localhost" || location.hostname === "127.0.0.1")) {
        window.addEventListener("load", () => navigator.serviceWorker.register("/sw.js").catch(() => {}));
    }
    let deferred = null;
    window.addEventListener("beforeinstallprompt", (e) => {
        e.preventDefault();
        deferred = e;
        document.getElementById("installApp")?.classList.remove("hidden");
    });
    document.getElementById("installApp")?.addEventListener("click", async () => {
        if (!deferred) return;
        deferred.prompt();
        await deferred.userChoice;
        deferred = null;
        document.getElementById("installApp").classList.add("hidden");
    });
})();
