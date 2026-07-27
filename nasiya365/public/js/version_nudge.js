/**
 * Version nudge — tells long-lived desk tabs when a new build is deployed.
 *
 * The desk is a single-page app: an open tab keeps running the JS it loaded until
 * a full reload. Hashed bundles fix the cache layer, but a tab that is never
 * reloaded still runs old code. This polls a cheap, never-cached endpoint for the
 * current bundle hash and, when it differs from the loaded one, shows a dismissible
 * toast with a Reload button. It never auto-reloads — that could drop an unsaved
 * form (e.g. a half-entered payment).
 *
 * Bundled into nasiya365.bundle.js; runs only on the desk (where `frappe` exists).
 */
(function () {
	"use strict";

	if (typeof window === "undefined" || !window.frappe || !frappe.call) return;

	var POLL_MS = 5 * 60 * 1000; // 5 minutes
	var MIN_GAP_MS = 60 * 1000; // don't poll more than once a minute on focus bursts
	var shown = false;
	var lastCheck = 0;

	// Basename of our own bundle script, e.g. "nasiya365.bundle.A1B2C3D4.js".
	// This is the loaded version; the server reports the current one to compare.
	function basename(url) {
		if (!url) return "";
		return String(url).split("?")[0].split("#")[0].split("/").pop();
	}

	function loadedVersion() {
		var el = document.querySelector('script[src*="nasiya365.bundle"]');
		if (el && el.src) return basename(el.src);
		if (document.currentScript && document.currentScript.src) {
			return basename(document.currentScript.src);
		}
		return "";
	}

	var LOADED = loadedVersion();
	// Unhashed/dev builds (no dist bundle on the page) give us nothing to compare — skip quietly.
	if (!LOADED || LOADED.indexOf("nasiya365.bundle") !== 0) return;

	function showToast() {
		if (shown) return;
		shown = true;

		var bar = document.createElement("div");
		bar.id = "nasiya-update-toast";
		bar.style.cssText = [
			"position:fixed", "bottom:20px", "right:20px", "z-index:99999",
			"background:#111827", "color:#fff", "padding:12px 16px",
			"border-radius:12px", "font-size:13px",
			"font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif",
			"box-shadow:0 8px 28px rgba(0,0,0,.35)",
			"display:flex", "align-items:center", "gap:12px", "max-width:90vw",
		].join(";");

		var msg = document.createElement("span");
		msg.textContent = "🔄 Доступно обновление Nasiya365";
		bar.appendChild(msg);

		var reload = document.createElement("button");
		reload.textContent = "Обновить";
		reload.style.cssText = [
			"background:#2563eb", "color:#fff", "border:none",
			"padding:6px 14px", "border-radius:8px", "font-size:12px",
			"font-weight:600", "cursor:pointer", "white-space:nowrap",
		].join(";");
		reload.addEventListener("click", function () {
			window.location.reload();
		});
		bar.appendChild(reload);

		var close = document.createElement("button");
		close.textContent = "✕";
		close.setAttribute("aria-label", "Закрыть");
		close.style.cssText = [
			"background:transparent", "color:#9ca3af", "border:none",
			"font-size:15px", "line-height:1", "cursor:pointer", "padding:0",
		].join(";");
		close.addEventListener("click", function () {
			bar.remove();
			// keep `shown` true so we don't nag again this session
		});
		bar.appendChild(close);

		(document.body || document.documentElement).appendChild(bar);
	}

	function check() {
		if (shown) return;
		var now = Date.now();
		if (now - lastCheck < MIN_GAP_MS) return;
		lastCheck = now;

		frappe.call({
			method: "nasiya365.api.app_meta.asset_version",
			callback: function (r) {
				var server = basename(r && r.message);
				if (server && server.indexOf("nasiya365.bundle") === 0 && server !== LOADED) {
					showToast();
				}
			},
			error: function () {
				/* transient — ignore, try again next tick */
			},
		});
	}

	setInterval(check, POLL_MS);

	document.addEventListener("visibilitychange", function () {
		if (document.visibilityState === "visible") check();
	});
	window.addEventListener("focus", check);
})();
