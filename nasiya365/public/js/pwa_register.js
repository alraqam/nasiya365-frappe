(function () {
	"use strict";

	var manifestPath = "/assets/nasiya365/manifest.json";
	var swPath = "/nasiya365_sw.min.js";

	// ── Manifest link ────────────────────────────────────────────────────────
	if (!document.querySelector('link[rel="manifest"]')) {
		var link = document.createElement("link");
		link.rel = "manifest";
		link.href = manifestPath;
		document.head.appendChild(link);
	}

	// ── Apple touch icon ─────────────────────────────────────────────────────
	if (!document.querySelector('link[rel="apple-touch-icon"]')) {
		var apple = document.createElement("link");
		apple.rel = "apple-touch-icon";
		apple.href = "/assets/nasiya365/icons/icon-192.svg";
		document.head.appendChild(apple);
	}

	// ── Service worker ────────────────────────────────────────────────────────
	if (!("serviceWorker" in navigator)) return;

	window.addEventListener("load", function () {
		navigator.serviceWorker.register(swPath, { scope: "/" }).catch(function () {
			/* ignore: HTTP, unsupported, blocked */
		});
	});

	// ── Install prompt banner ─────────────────────────────────────────────────
	// Capture the browser's beforeinstallprompt event so we can show our own
	// "Add to home screen" button rather than letting the browser decide timing.

	var _deferredPrompt = null;

	window.addEventListener("beforeinstallprompt", function (e) {
		e.preventDefault();
		_deferredPrompt = e;
		_showInstallBanner();
	});

	function _showInstallBanner() {
		// Only show on the Frappe desk, not on login or portal pages
		if (!document.body || !document.body.classList.contains("app-bootstrap")) {
			window.addEventListener("DOMContentLoaded", _showInstallBanner, { once: true });
			return;
		}
		// Don't show if already shown this session
		if (sessionStorage.getItem("nasiya_pwa_banner_shown")) return;

		var banner = document.createElement("div");
		banner.id = "nasiya-pwa-banner";
		banner.style.cssText = [
			"position:fixed", "bottom:16px", "left:50%", "transform:translateX(-50%)",
			"background:#0089FF", "color:#fff", "padding:10px 20px",
			"border-radius:24px", "font-size:13px", "font-family:Arial,sans-serif",
			"box-shadow:0 4px 16px rgba(0,137,255,.35)", "z-index:9999",
			"display:flex", "align-items:center", "gap:12px",
			"white-space:nowrap", "cursor:pointer",
		].join(";");

		banner.innerHTML =
			'<span>📲 Установить Nasiya365 на устройство</span>' +
			'<button id="nasiya-pwa-install" style="background:#fff;color:#0089FF;' +
			'border:none;padding:4px 14px;border-radius:16px;font-size:12px;' +
			'font-weight:bold;cursor:pointer;">Установить</button>' +
			'<button id="nasiya-pwa-dismiss" style="background:transparent;color:#fff;' +
			'border:none;font-size:16px;line-height:1;cursor:pointer;padding:0;">✕</button>';

		document.body.appendChild(banner);
		sessionStorage.setItem("nasiya_pwa_banner_shown", "1");

		document.getElementById("nasiya-pwa-install").addEventListener("click", function () {
			if (_deferredPrompt) {
				_deferredPrompt.prompt();
				_deferredPrompt.userChoice.then(function () {
					_deferredPrompt = null;
				});
			}
			banner.remove();
		});

		document.getElementById("nasiya-pwa-dismiss").addEventListener("click", function () {
			banner.remove();
		});
	}

	// Clean up after successful install
	window.addEventListener("appinstalled", function () {
		_deferredPrompt = null;
		var b = document.getElementById("nasiya-pwa-banner");
		if (b) b.remove();
	});
})();
