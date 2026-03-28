// Register Nasiya365 PWA: manifest + service worker (root scope via /nasiya365_sw.min.js).
(function () {
	const manifestPath = "/assets/nasiya365/manifest.json";
	const swPath = "/nasiya365_sw.min.js";

	if (!document.querySelector('link[rel="manifest"]')) {
		const link = document.createElement("link");
		link.rel = "manifest";
		link.href = manifestPath;
		document.head.appendChild(link);
	}

	if (!document.querySelector('link[rel="apple-touch-icon"]')) {
		const apple = document.createElement("link");
		apple.rel = "apple-touch-icon";
		apple.href = "/assets/frappe/images/frappe-framework-logo.png";
		document.head.appendChild(apple);
	}

	if (!("serviceWorker" in navigator)) {
		return;
	}

	window.addEventListener("load", function () {
		navigator.serviceWorker.register(swPath, { scope: "/" }).catch(function () {
			/* ignore — e.g. HTTP, unsupported, or blocked */
		});
	});
})();
