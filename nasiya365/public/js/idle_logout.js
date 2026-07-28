/**
 * Idle auto-logout — signs a desk user out after a period of inactivity.
 *
 * A left-open desk session otherwise stays valid until the (long) server-side
 * `session_expiry`. This client-side guard logs the user out after IDLE_MS of no
 * interaction (mouse, keyboard, scroll, touch, click) — no warning, straight to
 * the login screen. Activity in ANY tab keeps every tab alive via a shared
 * localStorage timestamp, so working in one tab never logs you out of another.
 *
 * Bundled into nasiya365.bundle.js; runs only on the desk for a real (non-Guest) user.
 */
(function () {
	"use strict";

	if (typeof window === "undefined" || !window.frappe || !frappe.session) return;
	if (!frappe.session.user || frappe.session.user === "Guest") return;

	var IDLE_MS = 20 * 60 * 1000; // 20 minutes of inactivity -> logout (no warning)
	var KEY = "nasiya_last_activity"; // shared across this origin's tabs
	var ACTIVITY_THROTTLE_MS = 1000; // process activity at most once per second
	var WRITE_THROTTLE_MS = 5000; // cap localStorage writes to once per 5s
	var timer = null;
	var lastActivity = 0;
	var lastWrite = 0;

	function now() {
		return new Date().getTime();
	}

	function loggedIn() {
		return frappe.session && frappe.session.user && frappe.session.user !== "Guest";
	}

	function readLast() {
		try {
			return parseInt(localStorage.getItem(KEY) || "0", 10) || 0;
		} catch (e) {
			return 0;
		}
	}

	function writeLast(t) {
		try {
			localStorage.setItem(KEY, String(t));
		} catch (e) {
			/* private mode / storage disabled — the per-tab timer still works */
		}
	}

	function goLogin() {
		window.location.href = "/login";
	}

	function logout() {
		if (timer) {
			clearTimeout(timer);
			timer = null;
		}
		try {
			if (frappe.app && typeof frappe.app.logout === "function") {
				frappe.app.logout();
				return;
			}
		} catch (e) {
			/* fall through to the explicit endpoint call */
		}
		// Fallback: kill the session server-side, then land on the login screen.
		frappe.call({ method: "logout", callback: goLogin, error: goLogin });
	}

	// (Re)start the countdown from "now + full idle window".
	function arm() {
		if (timer) clearTimeout(timer);
		timer = setTimeout(onTimeout, IDLE_MS);
	}

	function onTimeout() {
		if (!loggedIn()) return;
		var idle = now() - readLast();
		if (idle >= IDLE_MS) {
			logout();
		} else {
			// Another tab saw activity more recently — wait out the remainder.
			timer = setTimeout(onTimeout, IDLE_MS - idle);
		}
	}

	function onActivity() {
		var t = now();
		if (t - lastActivity < ACTIVITY_THROTTLE_MS) return;
		lastActivity = t;
		if (t - lastWrite > WRITE_THROTTLE_MS) {
			lastWrite = t;
			writeLast(t);
		}
		arm();
	}

	var EVENTS = ["mousemove", "mousedown", "keydown", "scroll", "touchstart", "click"];
	EVENTS.forEach(function (ev) {
		window.addEventListener(ev, onActivity, { passive: true });
	});

	// Activity in another tab bumps the shared timestamp — re-arm so this tab is
	// not logged out while the user is busy elsewhere.
	window.addEventListener("storage", function (e) {
		if (e.key === KEY) arm();
	});

	// Initialize.
	lastActivity = now();
	lastWrite = lastActivity;
	writeLast(lastActivity);
	arm();
})();
