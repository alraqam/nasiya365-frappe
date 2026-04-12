/**
 * Dynamic list-column picker for all Frappe list views.
 * Adds a "Колонки" button to every list page toolbar.
 * Preferences are stored in localStorage per user+doctype and survive refresh.
 *
 * Fixed columns (never in picker):
 *   • Subject  — row 0, the clickable title/name link (required for navigation)
 *   • Tag      — row 1, always present but visually hidden
 *
 * Everything else — Status indicator, all field columns, trailing Name — is
 * user-controllable via the picker.
 */
(function () {
	"use strict";

	const STORAGE_PREFIX = "nasiya_list_cols_v2_";
	const MAX_COLS = 8;
	// Virtual key representing the Status indicator column (not a real field)
	const STATUS_KEY = "__status__";

	const SKIP_TYPES = new Set([
		"Section Break", "Column Break", "Tab Break",
		"Table", "Table MultiSelect", "Fold", "Heading",
		"HTML", "HTML Editor", "Markdown Editor", "Code",
		"Signature", "Attach", "Attach Image",
		"Geolocation", "Password", "Button",
	]);

	/* ── storage ─────────────────────────────────────────────────── */

	function storage_key(doctype) {
		return STORAGE_PREFIX + (frappe.session.user || "guest") + "_" + doctype;
	}

	function load_cols(doctype) {
		try {
			const raw = localStorage.getItem(storage_key(doctype));
			return raw ? JSON.parse(raw) : null;
		} catch (_) { return null; }
	}

	function save_cols(doctype, cols) {
		try {
			if (cols && cols.length) {
				localStorage.setItem(storage_key(doctype), JSON.stringify(cols));
			} else {
				localStorage.removeItem(storage_key(doctype));
			}
		} catch (_) { }
	}

	/* ── meta helpers ────────────────────────────────────────────── */

	function has_indicator(doctype) {
		return typeof frappe.has_indicator === "function" && frappe.has_indicator(doctype);
	}

	function title_fieldname(doctype) {
		return frappe.get_meta(doctype)?.title_field || null;
	}

	/** Default column keys: STATUS_KEY (if applicable) + in_list_view fields */
	function default_col_keys(doctype) {
		const meta = frappe.get_meta(doctype);
		if (!meta) return [];
		const keys = [];
		if (has_indicator(doctype)) keys.push(STATUS_KEY);
		meta.fields
			.filter(f => f.in_list_view && f.fieldname && !SKIP_TYPES.has(f.fieldtype))
			.forEach(f => keys.push(f.fieldname));
		return keys;
	}

	/** All fields that can appear in the picker (excludes title field and skip types) */
	function available_options(doctype) {
		const meta = frappe.get_meta(doctype);
		if (!meta) return [];
		const title = title_fieldname(doctype);
		const opts = [];
		if (has_indicator(doctype)) {
			opts.push({ key: STATUS_KEY, label: "Статус (индикатор)", fieldtype: "— indicator —" });
		}
		meta.fields
			.filter(f =>
				f.fieldname &&
				f.label &&
				!SKIP_TYPES.has(f.fieldtype) &&
				!f.hidden &&
				f.fieldname !== title
			)
			.forEach(f => opts.push({ key: f.fieldname, label: f.label, fieldtype: f.fieldtype, df: f }));
		return opts;
	}

	/* ── ListView patches ────────────────────────────────────────── */

	function apply_patches() {
		if (!frappe.views || !frappe.views.ListView) return false;
		if (frappe.views.ListView._nasiya_col_picker_patched) return true;
		frappe.views.ListView._nasiya_col_picker_patched = true;

		/* Patch 1: setup_columns */
		const _orig = frappe.views.ListView.prototype.setup_columns;

		frappe.views.ListView.prototype.setup_columns = function () {
			_orig.call(this);

			const stored = load_cols(this.doctype);
			if (!stored || !stored.length) return;

			const meta = frappe.get_meta(this.doctype);
			if (!meta) return;

			const field_map = Object.fromEntries(
				meta.fields.filter(f => f.fieldname).map(f => [f.fieldname, f])
			);

			// Subject (0) + Tag (1) are always fixed — everything else is user-controlled.
			const fixed = this.columns.slice(0, 2);

			const user_cols = stored.map(key => {
				if (key === STATUS_KEY) return { type: "Status" };
				if (field_map[key]) return { type: "Field", df: field_map[key] };
				return null;
			}).filter(Boolean);

			this.columns = [...fixed, ...user_cols];
		};

		/* Patch 2: inject toolbar button */
		const _orig_setup_view = frappe.views.ListView.prototype.setup_view;

		frappe.views.ListView.prototype.setup_view = function () {
			_orig_setup_view.call(this);
			_inject_button(this);
		};

		return true;
	}

	/* ── button ──────────────────────────────────────────────────── */

	function _inject_button(lv) {
		if (!lv.page || lv._nasiya_col_btn_added) return;
		lv._nasiya_col_btn_added = true;
		lv.page.add_inner_button(__("Колонки"), () => open_picker(lv));
	}

	/* ── picker dialog ───────────────────────────────────────────── */

	function open_picker(lv) {
		const dt = lv.doctype;
		const opts = available_options(dt);
		const defaults = default_col_keys(dt);
		const saved = load_cols(dt);
		const saved_order = saved || defaults;
		const saved_set = new Set(saved_order);

		// Saved keys first (in order), remaining opts below
		const sorted_opts = [
			...saved_order.map(k => opts.find(o => o.key === k)).filter(Boolean),
			...opts.filter(o => !saved_set.has(o.key)),
		];
		const current = new Set(saved_order);

		function make_row(o) {
			const checked = current.has(o.key) ? "checked" : "";
			const def_badge = defaults.includes(o.key)
				? `<span class="nasiya-def-badge">по умолч.</span>` : "";
			return `
				<div class="nasiya-col-row" draggable="true" data-key="${frappe.utils.escape_html(o.key)}">
					<span class="nasiya-drag-handle" title="Перетащите для сортировки">⠿</span>
					<input type="checkbox" ${checked}>
					<span class="nasiya-col-label">${frappe.utils.escape_html(o.label)}${def_badge}</span>
					<span class="nasiya-col-type">${frappe.utils.escape_html(o.fieldtype)}</span>
				</div>`;
		}

		const d = new frappe.ui.Dialog({
			title: __("Колонки") + " · " + dt,
			fields: [{
				fieldtype: "HTML",
				fieldname: "picker_html",
				options: `
					<style>
						#nasiya-col-list { max-height:380px;overflow-y:auto;
							border:1px solid var(--border-color);border-radius:6px;padding:4px; }
						.nasiya-col-row { display:flex;align-items:center;gap:8px;
							padding:5px 8px;border-radius:4px;margin:0;
							user-select:none;cursor:default;transition:background .1s; }
						.nasiya-col-row:hover { background:var(--fg-hover,#f3f4f6); }
						.nasiya-col-row.drag-over { border-top:2px solid var(--primary,#4f46e5); }
						.nasiya-col-row.dragging { opacity:.35; }
						.nasiya-drag-handle { cursor:grab;color:var(--text-muted);
							font-size:16px;flex-shrink:0;line-height:1; }
						.nasiya-drag-handle:active { cursor:grabbing; }
						.nasiya-col-row input[type=checkbox] { width:14px;height:14px;
							cursor:pointer;flex-shrink:0;accent-color:var(--primary,#4f46e5); }
						.nasiya-col-label { flex:1;min-width:0;overflow:hidden;
							text-overflow:ellipsis;white-space:nowrap; }
						.nasiya-col-type { color:var(--text-muted);font-size:11px;flex-shrink:0; }
						.nasiya-def-badge { font-size:10px;padding:1px 5px;border-radius:3px;
							margin-left:4px;background:var(--blue-100,#dbeafe);
							color:var(--blue-600,#2563eb); }
					</style>
					<div style="display:flex;justify-content:space-between;align-items:center;
					            margin-bottom:10px;font-size:12px;color:var(--text-muted);">
						<span>Отметьте и перетащите для изменения порядка</span>
						<span id="nasiya-col-count" style="font-weight:600;"></span>
					</div>
					<div id="nasiya-col-list">${sorted_opts.map(make_row).join("")}</div>`,
			}],
			primary_action_label: __("Применить"),
			primary_action() {
				const selected = [...d.$wrapper.find("#nasiya-col-list .nasiya-col-row")]
					.filter(el => el.querySelector("input").checked)
					.map(el => el.dataset.key);

				if (!selected.length) {
					frappe.show_alert({ message: __("Выберите хотя бы одну колонку"), indicator: "orange" });
					return;
				}
				if (selected.length > MAX_COLS) {
					frappe.show_alert({
						message: __("Максимум {0} колонок").replace("{0}", MAX_COLS),
						indicator: "orange",
					});
					return;
				}
				save_cols(dt, selected);
				d.hide();
				lv.refresh_columns(lv.meta, lv.list_view_settings);
			},
			secondary_action_label: __("По умолчанию"),
			secondary_action() {
				save_cols(dt, null);
				d.hide();
				lv.refresh_columns(lv.meta, lv.list_view_settings);
			},
		});

		d.show();

		// drag-and-drop
		const list_el = d.$wrapper.find("#nasiya-col-list")[0];
		let drag_src = null;

		list_el.addEventListener("dragstart", e => {
			drag_src = e.target.closest(".nasiya-col-row");
			if (drag_src) { drag_src.classList.add("dragging"); e.dataTransfer.effectAllowed = "move"; }
		});
		list_el.addEventListener("dragend", () => {
			list_el.querySelectorAll(".nasiya-col-row").forEach(r => r.classList.remove("dragging", "drag-over"));
			drag_src = null;
		});
		list_el.addEventListener("dragover", e => {
			e.preventDefault();
			e.dataTransfer.dropEffect = "move";
			const target = e.target.closest(".nasiya-col-row");
			list_el.querySelectorAll(".nasiya-col-row").forEach(r => r.classList.remove("drag-over"));
			if (target && target !== drag_src) target.classList.add("drag-over");
		});
		list_el.addEventListener("drop", e => {
			e.preventDefault();
			const target = e.target.closest(".nasiya-col-row");
			if (!target || !drag_src || target === drag_src) return;
			list_el.insertBefore(drag_src, target);
			list_el.querySelectorAll(".nasiya-col-row").forEach(r => r.classList.remove("drag-over"));
			update_count();
		});

		function update_count() {
			const n = list_el.querySelectorAll("input:checked").length;
			const $c = d.$wrapper.find("#nasiya-col-count");
			$c.text(n + " / " + MAX_COLS);
			$c.css("color", n > MAX_COLS ? "var(--red-500,#ef4444)" : "var(--text-muted)");
		}
		d.$wrapper.on("change", "input[type=checkbox]", update_count);
		update_count();
	}

	/* ── bootstrap ───────────────────────────────────────────────── */

	function init() {
		if (apply_patches()) return;
		const on_change = function () {
			if (apply_patches()) $(document).off("page-change", on_change);
		};
		$(document).on("page-change", on_change);
	}

	$(document).ready(init);
})();
