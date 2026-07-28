/**
 * App-wide JS bundle entry point.
 *
 * Frappe's esbuild pipeline compiles this into a content-hashed file
 * (nasiya365.bundle.<HASH>.js, recorded in assets.json), so every `bench build`
 * produces a new URL. That lets browsers and the service worker fetch fresh code
 * after a deploy without a manual cache clear — unlike a fixed /assets/... path
 * served with `Cache-Control: immutable`.
 *
 * hooks.py references this by its logical name: app_include_js = ["nasiya365.bundle.js"].
 * Each import below is a plain side-effect script (no exports); order preserved
 * from the previous app_include_js list.
 */
import "./installment_calculator.js";
import "./pwa_register.js";
import "./list_column_picker.js";
import "./version_nudge.js";
import "./idle_logout.js";
