const PREVIEW_DEBOUNCE_MS = 200;
function nasiya_has_field(frm, fieldname) {
	return Boolean(frm?.fields_dict?.[fieldname]);
}

frappe.ui.form.on("Installment Plan", {
	onload(frm) {
		frm._nasiya_preview_timer = null;
	},

	refresh(frm) {
		frm.$wrapper.addClass("nasiya-ip-wizard");
		set_stock_entry_filter(frm);
		bind_stock_entry_line_picker(frm);
		set_sales_order_filter(frm);
		setup_bnpl_actions(frm);
		load_risk_panel(frm);
		schedule_preview_refresh(frm, 0);
	},

	customer(frm) {
		set_stock_entry_filter(frm);
		set_sales_order_filter(frm);
		if (!frm.doc.customer) {
			frm.set_value("stock_entry", "");
			frm.set_value("sales_order", "");
			if (nasiya_has_field(frm, "customer_phone")) {
				frm.set_value("customer_phone", "");
			}
			clear_panels(frm);
			return;
		}
		load_risk_panel(frm);
	},

	stock_entry(frm) {
		if (!frm.doc.stock_entry) return;
		// Defer: Frappe sets the Link on `awesomplete-select`; our handler runs after
		// the control's handler so `frm._nasiya_se_picked_imei` is filled before we read it.
		setTimeout(() => nasiya_fetch_stock_entry_details(frm), 0);
	},

	sales_order(frm) {
		if (!frm.doc.sales_order) return;
		frappe.call({
			method:
				"nasiya365.nasiya365.doctype.installment_plan.installment_plan.get_sales_order_details",
			args: { sales_order: frm.doc.sales_order },
			callback(r) {
				if (!r.message) return;
				const so = r.message;
				frm.set_value("principal_amount", so.total_amount);
				if (!frm.doc.customer && so.customer) frm.set_value("customer", so.customer);
				if (so.product_name) frm.set_value("product_name", so.product_name);
				if (so.imei) {
					frm.set_value("imei", (so.imei || "").trim());
				}
				schedule_preview_refresh(frm, 0);
			},
		});
	},

	principal_amount: schedule_preview_change,
	down_payment: schedule_preview_change,
	interest_rate: schedule_preview_change,
	number_of_installments: schedule_preview_change,
	frequency: schedule_preview_change,
	start_date: schedule_preview_change,
});

const NASIYA_SCHEDULE_CHILD = "Installment Schedule";

/** Cascade schedule dates from the edited row (same step as server: week / 2 weeks / month). */
function nasiya_cascade_schedule_from_row(frm, edited_row_name, anchor_date) {
	const schedule = frm.doc.schedule || [];
	if (schedule.length < 2) {
		const only = schedule[0];
		if (only && only.name === edited_row_name) {
			frm._nasiya_skip_schedule_preview = true;
			try {
				frm.set_value("start_date", anchor_date);
			} finally {
				frm._nasiya_skip_schedule_preview = false;
			}
			frm.set_value("end_date", anchor_date);
		}
		return;
	}

	const rows = [...schedule].sort((a, b) => (a.idx || 0) - (b.idx || 0));
	const pos = rows.findIndex((r) => r.name === edited_row_name);
	if (pos === -1) return;

	frm._nasiya_cascade_schedule = true;
	try {
		let prev = anchor_date;
		for (let i = pos + 1; i < rows.length; i++) {
			const r = rows[i];
			prev = nasiya_schedule_step_date(prev, frm.doc.frequency);
			frappe.model.set_value(r.doctype || NASIYA_SCHEDULE_CHILD, r.name, "due_date", prev);
		}
		if (pos === 0) {
			frm._nasiya_skip_schedule_preview = true;
			try {
				frm.set_value("start_date", anchor_date);
			} finally {
				frm._nasiya_skip_schedule_preview = false;
			}
		}
		const last_row = rows[rows.length - 1];
		const last_due = frappe.model.get_value(
			last_row.doctype || NASIYA_SCHEDULE_CHILD,
			last_row.name,
			"due_date"
		);
		if (last_due) frm.set_value("end_date", last_due);
	} finally {
		frm._nasiya_cascade_schedule = false;
	}
}

/** Set the same payment amount on all schedule rows after the edited row (equal installments). */
function nasiya_cascade_amount_from_row(frm, edited_row_name, anchor_amount) {
	const amt = flt(anchor_amount);
	const schedule = frm.doc.schedule || [];
	if (schedule.length < 2) return;

	const rows = [...schedule].sort((a, b) => (a.idx || 0) - (b.idx || 0));
	const pos = rows.findIndex((r) => r.name === edited_row_name);
	if (pos === -1) return;

	frm._nasiya_cascade_schedule = true;
	try {
		for (let i = pos + 1; i < rows.length; i++) {
			const r = rows[i];
			frappe.model.set_value(r.doctype || NASIYA_SCHEDULE_CHILD, r.name, "amount", amt);
		}
	} finally {
		frm._nasiya_cascade_schedule = false;
	}
}

/**
 * Implied monthly % from current schedule sums (same simple-interest model as server:
 * total_interest = financed * (interest_rate/100) * n  →  rate = 100 * total_interest / (financed * n)).
 * Uses _nasiya_skip_schedule_preview so updating interest_rate does not rebuild the grid.
 */
function nasiya_recalc_interest_rate_from_schedule(frm) {
	const sched = frm.doc.schedule || [];
	if (!sched.length) return;

	const principal = flt(frm.doc.principal_amount);
	if (principal <= 0) return;

	const down = flt(frm.doc.down_payment || 0);
	const financed = principal - down;
	const n = cint(frm.doc.number_of_installments) || sched.length;
	if (financed <= 0 || n <= 0) return;

	// Exclude row 0 (down payment) — it is not part of total_amount / financed amount.
	const schedule_sum = sched.reduce((acc, r) => cint(r.installment_number) === 0 ? acc : acc + flt(r.amount), 0);
	const total_interest_impl = schedule_sum - financed;
	let rate_pct = (100 * total_interest_impl) / (financed * n);
	if (!Number.isFinite(rate_pct) || rate_pct < 0) rate_pct = 0;

	frm._nasiya_skip_schedule_preview = true;
	try {
		frm.set_value("financed_amount", flt(financed));
		frm.set_value("total_interest", flt(Math.max(0, total_interest_impl)));
		frm.set_value("total_amount", flt(schedule_sum));
		frm.set_value("installment_amount", flt(schedule_sum / n));
		frm.set_value("remaining_balance", flt(schedule_sum - flt(frm.doc.paid_amount || 0)));
		frm.set_value("interest_rate", flt(rate_pct, 6));
	} finally {
		frm._nasiya_skip_schedule_preview = false;
	}
}

/**
 * Child Date edits go through frappe.model.trigger, not only script_manager.
 * model.on fires after the value is on the doc; defer one tick so grid/model match.
 */
frappe.model.on(NASIYA_SCHEDULE_CHILD, "due_date", function (fieldname, value, doc) {
	if (fieldname !== "due_date" || !doc) return;
	if (doc.parenttype !== "Installment Plan" || doc.parentfield !== "schedule") return;
	// Row 0 is the down payment — editing its date should not cascade to regular installments.
	if (cint(doc.installment_number) === 0) return;
	const frm = cur_frm;
	if (!frm || frm.doctype !== "Installment Plan" || frm.docname !== doc.parent) return;
	if (frm._nasiya_cascade_schedule) return;

	const anchor = value || doc.due_date;
	if (!anchor) return;

	const parent_name = doc.parent;
	const row_name = doc.name;
	setTimeout(() => {
		const f = cur_frm;
		if (!f || f.doctype !== "Installment Plan" || f.docname !== parent_name) return;
		if (f._nasiya_cascade_schedule) return;
		const anchor_now =
			frappe.model.get_value(NASIYA_SCHEDULE_CHILD, row_name, "due_date") || anchor;
		nasiya_cascade_schedule_from_row(f, row_name, anchor_now);
	}, 0);
});

frappe.model.on(NASIYA_SCHEDULE_CHILD, "amount", function (fieldname, value, doc) {
	if (fieldname !== "amount" || !doc) return;
	if (doc.parenttype !== "Installment Plan" || doc.parentfield !== "schedule") return;
	const frm = cur_frm;
	if (!frm || frm.doctype !== "Installment Plan" || frm.docname !== doc.parent) return;
	if (frm._nasiya_cascade_schedule) return;

	const anchor =
		value !== undefined && value !== null && value !== ""
			? value
			: doc.amount;
	if (anchor === undefined || anchor === null || anchor === "") return;

	const parent_name = doc.parent;
	const row_name = doc.name;
	setTimeout(() => {
		const f = cur_frm;
		if (!f || f.doctype !== "Installment Plan" || f.docname !== parent_name) return;
		if (f._nasiya_cascade_schedule) return;
		const raw = frappe.model.get_value(NASIYA_SCHEDULE_CHILD, row_name, "amount");
		const anchor_now = flt(raw !== undefined && raw !== null ? raw : anchor);
		nasiya_cascade_amount_from_row(f, row_name, anchor_now);
		nasiya_recalc_interest_rate_from_schedule(f);
	}, 0);
});

function nasiya_schedule_step_date(from_date, frequency) {
	const freq = (frequency || "").toString();
	if (freq.includes("Еженедельно")) {
		return nasiya_add_days_iso(from_date, 7);
	}
	if (freq.includes("две недели")) {
		return nasiya_add_days_iso(from_date, 14);
	}
	return nasiya_add_months_iso(from_date, 1);
}

function nasiya_add_days_iso(date_str, days_add) {
	const p = String(date_str).split("-");
	if (p.length !== 3) return date_str;
	const dt = new Date(+p[0], +p[1] - 1, +p[2]);
	dt.setDate(dt.getDate() + days_add);
	const y = dt.getFullYear();
	const m = String(dt.getMonth() + 1).padStart(2, "0");
	const d = String(dt.getDate()).padStart(2, "0");
	return `${y}-${m}-${d}`;
}

/** Calendar-month step aligned with server `add_months` (end-of-month clamp). */
function nasiya_add_months_iso(date_str, months_add) {
	const p = String(date_str).split("-");
	if (p.length !== 3) return date_str;
	const d0 = parseInt(p[2], 10);
	let y = parseInt(p[0], 10);
	let m = parseInt(p[1], 10);
	const total = y * 12 + (m - 1) + months_add;
	y = Math.floor(total / 12);
	m = (total % 12) + 1;
	const lastDay = new Date(y, m, 0).getDate();
	const d = Math.min(d0, lastDay);
	return `${y}-${String(m).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
}

function schedule_preview_change(frm) {
	if (frm._nasiya_skip_schedule_preview) return;
	schedule_preview_refresh(frm, PREVIEW_DEBOUNCE_MS);
}

function apply_stock_entry_item(frm, item) {
	if (flt(item.amount) > 0) {
		frm.set_value("principal_amount", flt(item.amount));
	}
	if (item.product_name) frm.set_value("product_name", item.product_name);
	if (item.imei) frm.set_value("imei", (item.imei || "").trim());
	schedule_preview_refresh(frm, 0);
}

function pick_stock_entry_item(frm, items) {
	const rows = items.map((item, idx) => {
		const name = frappe.utils.escape_html(item.product_name || item.product || "—");
		const color = frappe.utils.escape_html(item.color || "—");
		const storage = frappe.utils.escape_html(item.storage || "—");
		const imei = frappe.utils.escape_html(item.imei || "—");
		return `<tr>
			<td>${name}</td>
			<td>${color}</td>
			<td>${storage}</td>
			<td><code>${imei}</code></td>
			<td><button class="btn btn-primary btn-sm pick-item" data-idx="${idx}">${__("Выбрать")}</button></td>
		</tr>`;
	}).join("");

	const html = `<table class="table table-bordered table-sm" style="margin-bottom:0">
		<thead><tr>
			<th>${__("Товар")}</th>
			<th>${__("Цвет")}</th>
			<th>${__("Память")}</th>
			<th>${__("IMEI")}</th>
			<th></th>
		</tr></thead>
		<tbody>${rows}</tbody>
	</table>`;

	const d = new frappe.ui.Dialog({
		title: __("Выберите товар"),
		fields: [{ fieldtype: "HTML", fieldname: "items_table" }],
	});

	d.fields_dict.items_table.$wrapper.html(html);

	d.$wrapper.on("click", ".pick-item", function () {
		const idx = parseInt($(this).data("idx"));
		d.hide();
		apply_stock_entry_item(frm, items[idx]);
	});

	d.show();
}

function set_stock_entry_filter(frm) {
	frm.set_query("stock_entry", () => ({
		query: "nasiya365.api.bnpl_dashboard.installment_plan_stock_entry_query",
		filters: { installment_plan: frm.doc.name || "" },
	}));
}

/** Parse IMEI from link row label: "Product — IMEI · color/storage (STE-…)" */
function read_imei_from_label_string(label) {
	if (!label) return "";
	const sep = " — ";
	const i = label.indexOf(sep);
	if (i === -1) return "";
	const rest = label.slice(i + sep.length);
	const stop = rest.indexOf(" · ");
	const imeiPart = (stop === -1 ? rest : rest.slice(0, stop)).trim();
	if (!imeiPart || imeiPart === "—") return "";
	return imeiPart;
}

function nasiya_fetch_stock_entry_details(frm) {
	frappe.call({
		method: "nasiya365.api.bnpl_dashboard.get_stock_entry_details_for_installment_plan",
		args: {
			stock_entry: frm.doc.stock_entry,
			installment_plan: frm.doc.name || "",
		},
		callback(r) {
			if (!r.message) return;
			const d = r.message;
			const items = d.free_items || [];
			if (items.length > 1) {
				pick_stock_entry_item(frm, items);
			} else if (items.length === 1) {
				apply_stock_entry_item(frm, items[0]);
			}
		},
	});
}

function bind_stock_entry_line_picker(frm) {
	// No-op: stock_entry now links to Stock Entry (parent); item selection handled by picker dialog.
}

function set_sales_order_filter(frm) {
	frm.set_query("sales_order", () => {
		const filters = { docstatus: 1 };
		if (frm.doc.customer) filters.customer = frm.doc.customer;
		return { filters };
	});
}

function clear_panels(frm) {
	render_payment_preview(frm, null);
	render_risk_html(frm, null);
}

function load_risk_panel(frm) {
	if (!frm.doc.customer) {
		render_risk_html(frm, null);
		if (nasiya_has_field(frm, "customer_phone")) {
			frm.set_value("customer_phone", "");
		}
		return;
	}
	frappe.call({
		method: "nasiya365.api.bnpl_dashboard.get_client_risk_snapshot",
		args: { customer: frm.doc.customer },
		callback(r) {
			const m = r.message;
			if (!m) return;
			if (nasiya_has_field(frm, "customer_phone")) {
				frm.set_value("customer_phone", m.primary_phone || "");
			}
			render_risk_html(frm, m);
		},
	});
}

function render_risk_html(frm, m) {
	const el = frm.fields_dict.op_risk_panel?.$wrapper;
	if (!el) return;
	if (!m) {
		el.html(`<div class="text-muted small">${__("Выберите клиента")}</div>`);
		return;
	}
	const overdue_cls = cint(m.overdue_loans) > 0 ? "nasiya-ip-risk-warn" : "";
	el.html(`
		<div class="nasiya-ip-risk-card">
			<div class="title">${__("Риск и лимит")}</div>
			<div class="row"><span>${__("Скоринг")}</span><span>${frappe.utils.escape_html(String(m.risk_score ?? "—"))} · ${frappe.utils.escape_html(m.risk_level || "")}</span></div>
			<div class="row"><span>${__("Активные займы")}</span><span>${cint(m.active_loans)}</span></div>
			<div class="row"><span>${__("Просрочено")}</span><span class="${overdue_cls}">${cint(m.overdue_loans)}</span></div>
			<div class="row"><span>${__("Доступный лимит")}</span><span>${
				m.unlimited_credit
					? `<span class="text-muted">${__("Без ограничений")}</span>`
					: format_currency(m.available_limit)
			}</span></div>
			<div class="row"><span>${__("Текущий долг")}</span><span>${format_currency(m.total_debt)}</span></div>
		</div>
	`);
}

function render_payment_preview(frm, preview) {
	const el = frm.fields_dict.op_payment_preview?.$wrapper;
	if (!el) return;
	if (!preview) {
		el.html(
			`<div class="nasiya-ip-preview-card text-muted small">${__("Укажите сумму, срок и дату — расчёт появится автоматически")}</div>`
		);
		return;
	}
	const period_lbl =
		frm.doc.frequency && String(frm.doc.frequency).includes("Еженедельно")
			? __("Платёж (нед.)")
			: frm.doc.frequency && String(frm.doc.frequency).includes("две недели")
				? __("Платёж (2 нед.)")
				: __("Платёж в месяц");

	el.html(`
		<div class="nasiya-ip-preview-card">
			<div class="nasiya-ip-kicker">${period_lbl}</div>
			<div class="nasiya-ip-hero">${format_currency(preview.installment_amount)}</div>
			<div class="nasiya-ip-preview-grid">
				<div><div class="label">${__("К оплате всего")}</div><div class="value">${format_currency(preview.total_amount)}</div></div>
				<div><div class="label">${__("Проценты")}</div><div class="value">${format_currency(preview.total_interest)}</div></div>
				<div><div class="label">${__("Сумма кредита")}</div><div class="value">${format_currency(preview.financed_amount)}</div></div>
				<div><div class="label">${__("Последний платёж")}</div><div class="value">${frappe.format(preview.end_date, { fieldtype: "Date" })}</div></div>
			</div>
		</div>
	`);
}

function schedule_preview_refresh(frm, delay) {
	if (frm._nasiya_preview_timer) clearTimeout(frm._nasiya_preview_timer);
	const run = () => maybe_generate_schedule(frm, false);
	if (delay) frm._nasiya_preview_timer = setTimeout(run, delay);
	else run();
}

function has_schedule_payment_activity(schedule) {
	return (schedule || []).some((row) => {
		const st = (row.status || "").toString().trim();
		return flt(row.paid_amount) > 0 || st === "Оплачен" || st === "Частично";
	});
}

function maybe_generate_schedule(frm, force_regenerate) {
	const d = frm.doc;
	if (!d.principal_amount || !d.number_of_installments || !d.start_date) {
		render_payment_preview(frm, null);
		return;
	}
	const is_new_plan = !!frm.is_new();

	// Sequence counter: ignore any callback from an older call that arrives late.
	if (!frm._nasiya_preview_seq) frm._nasiya_preview_seq = 0;
	const seq = ++frm._nasiya_preview_seq;

	frappe.call({
		method:
			"nasiya365.nasiya365.doctype.installment_plan.installment_plan.calculate_installment_preview",
		args: {
			principal: d.principal_amount,
			down_payment: flt(d.down_payment),
			interest_rate: d.interest_rate || 0,
			num_installments: d.number_of_installments,
			frequency: d.frequency || "Ежемесячно",
			start_date: d.start_date,
		},
		callback(r) {
			// Stale response from a superseded call — discard.
			if (seq !== frm._nasiya_preview_seq) return;
			if (!r.message) return;
			const preview = r.message;
			render_payment_preview(frm, preview);

			// Always read frm.doc directly — `d` may be stale if Frappe replaced
			// frm.doc during the async round-trip (e.g. after a save/reload).
			const cur = frm.doc;

			// Never overwrite a график that already has payment allocations.
			if (has_schedule_payment_activity(cur.schedule)) return;

			frm.set_value("financed_amount", preview.financed_amount);
			frm.set_value("total_interest", preview.total_interest);
			frm.set_value("total_amount", preview.total_amount);
			frm.set_value("installment_amount", preview.installment_amount);
			frm.set_value("end_date", preview.end_date);
			frm.set_value("remaining_balance", preview.total_amount - flt(cur.paid_amount || 0));

			// Keep existing schedule rows unless the user explicitly forced a rebuild.
			// New plans with an already-built schedule must also be protected — clearing
			// the grid while a child-row quick-form dialog is open destroys the dialog
			// DOM and leaves the Bootstrap backdrop ("white bg") orphaned.
			const has_existing_schedule = (cur.schedule || []).length > 0;
			if (!force_regenerate && has_existing_schedule) return;

			frm.clear_table("schedule");
			(preview.schedule || []).forEach((row) => {
				const child = frm.add_child("schedule");
				child.installment_number = row.installment_number;
				child.due_date = row.due_date;
				child.amount = row.amount;
				child.status = row.status || "Ожидает";
				child.paid_amount = row.paid_amount || 0;
			});
			frm.refresh_field("schedule");
		},
	});
}

function setup_bnpl_actions(frm) {
	if (frm.is_new()) {
		frm.set_intro(
			__(
				"Минимум полей: клиент, сумма покупки, число платежей. График и превью обновляются автоматически."
			),
			"blue"
		);
	} else {
		frm.set_intro(null);
	}

	frm.clear_custom_buttons();

	frm.add_custom_button(
		__("Симулятор 6 / 9 / 12"),
		() => open_term_simulator(frm),
		__("BNPL")
	);
	frm.add_custom_button(
		__("Сформировать график"),
		() => generate_schedule_now(frm),
		__("BNPL")
	);
	frm.add_custom_button(__("Отправить OTP"), () => send_otp(frm), __("BNPL"));
	frm.add_custom_button(__("Предпросмотр / печать"), () => frm.print_doc(), __("BNPL"));
	if (frm.doc.docstatus === 0) {
		frm.add_custom_button(__("Сохранить черновик"), () => save_draft(frm), __("BNPL"));
		frm
			.add_custom_button(__("Активировать (провести)"), () => activate_plan(frm), __("BNPL"))
			.addClass("btn-primary");
	}
}

function generate_schedule_now(frm) {
	if (!frm.doc.principal_amount || !frm.doc.number_of_installments || !frm.doc.start_date) {
		frappe.show_alert({ message: __("Заполните сумму, количество платежей и дату"), indicator: "orange" });
		return;
	}
	if (has_schedule_payment_activity(frm.doc.schedule)) {
		frappe.show_alert({
			message: __("Нельзя перегенерировать график: в нём уже есть оплаты"),
			indicator: "orange",
		});
		return;
	}
	maybe_generate_schedule(frm, true);
	frappe.show_alert({ message: __("График обновлён"), indicator: "green" });
}

function save_draft(frm) {
	frm.set_value("status", "Черновик");
	frm.save();
}

function activate_plan(frm) {
	const go = () => {
		if (frm.doc.docstatus === 0) frm.savesubmit();
	};
	if (!frm.doc.schedule || !frm.doc.schedule.length) {
		frappe.confirm(__("График пуст. Сформировать из текущих полей?"), () => {
			schedule_preview_refresh(frm, 0);
			setTimeout(go, 400);
		});
		return;
	}
	go();
}

function send_otp(frm) {
	if (!frm.doc.customer) {
		frappe.show_alert({ message: __("Сначала выберите клиента"), indicator: "red" });
		return;
	}
	frappe.call({
		method: "nasiya365.nasiya365.doctype.installment_plan.installment_plan.send_installment_plan_otp",
		args: { customer: frm.doc.customer },
		callback(r) {
			const m = r.message || {};
			frappe.show_alert({ message: m.message || __("OTP"), indicator: "orange" });
		},
	});
}

function open_term_simulator(frm) {
	const p = flt(frm.doc.principal_amount);
	const down = flt(frm.doc.down_payment || 0);
	const rate = flt(frm.doc.interest_rate || 0);
	const start = frm.doc.start_date || frappe.datetime.get_today();
	const freq = frm.doc.frequency || "Ежемесячно";

	if (!p || !frm.doc.start_date) {
		frappe.show_alert({
			message: __("Укажите сумму покупки и дату первого платежа"),
			indicator: "orange",
		});
		return;
	}

	frappe.call({
		method:
			"nasiya365.nasiya365.doctype.installment_plan.installment_plan.compare_installment_terms",
		args: {
			principal: p,
			down_payment: down,
			interest_rate: rate,
			frequency: freq,
			start_date: start,
		},
		callback(r) {
			const list = r.message || [];
			const rows = [6, 9, 12].map((n, i) => {
				const x = list[i] || {};
				return `<tr><td>${n} ${__("мес.")}</td><td class="text-end"><b>${format_currency(
					x.installment_amount || 0
				)}</b></td><td class="text-end">${format_currency(x.total_amount || 0)}</td></tr>`;
			});
			const d = new frappe.ui.Dialog({
				title: __("Симулятор срока"),
				fields: [{ fieldtype: "HTML", fieldname: "h" }],
				primary_action_label: __("Закрыть"),
				primary_action: () => d.hide(),
			});
			d.fields_dict.h.$wrapper.html(`
				<table class="table table-bordered">
					<thead><tr><th>${__("Срок")}</th><th class="text-end">${__("Платёж")}</th><th class="text-end">${__("Всего")}</th></tr></thead>
					<tbody>${rows.join("")}</tbody>
				</table>
			`);
			d.show();
		},
	});
}
