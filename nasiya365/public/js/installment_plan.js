const PREVIEW_DEBOUNCE_MS = 200;

frappe.ui.form.on("Installment Plan", {
	onload(frm) {
		frm._nasiya_preview_timer = null;
	},

	refresh(frm) {
		frm.$wrapper.addClass("nasiya-ip-wizard");
		set_stock_entry_filter(frm);
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
			frm.set_value("customer_phone", "");
			clear_panels(frm);
			return;
		}
		load_risk_panel(frm);
	},

	stock_entry(frm) {
		if (!frm.doc.stock_entry) return;
		frappe.call({
			method:
				"nasiya365.api.bnpl_dashboard.get_stock_entry_details_for_installment_plan",
			args: {
				stock_entry: frm.doc.stock_entry,
				installment_plan: frm.doc.name || "",
			},
			callback(r) {
				if (!r.message) return;
				const d = r.message;
				if (flt(d.total_amount) > 0) {
					frm.set_value("principal_amount", d.total_amount);
				}
				if (d.product_name) frm.set_value("product_name", d.product_name);
				if (d.imei) {
					const full = (d.imei || "").trim();
					frm.set_value("imei", full.length >= 6 ? full.slice(-6) : full);
				}
				schedule_preview_refresh(frm, 0);
			},
		});
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
					const full = (so.imei || "").trim();
					frm.set_value("imei", full.length >= 6 ? full.slice(-6) : full);
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

function schedule_preview_change(frm) {
	schedule_preview_refresh(frm, PREVIEW_DEBOUNCE_MS);
}

function set_stock_entry_filter(frm) {
	frm.set_query("stock_entry", () => ({
		query: "nasiya365.api.bnpl_dashboard.installment_plan_stock_entry_query",
		filters: { installment_plan: frm.doc.name || "" },
	}));
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
		frm.set_value("customer_phone", "");
		return;
	}
	frappe.call({
		method: "nasiya365.api.bnpl_dashboard.get_client_risk_snapshot",
		args: { customer: frm.doc.customer },
		callback(r) {
			const m = r.message;
			if (!m) return;
			frm.set_value("customer_phone", m.primary_phone || "");
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
			<div class="row"><span>${__("Доступный лимит")}</span><span>${format_currency(m.available_limit)}</span></div>
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
	const run = () => maybe_generate_schedule(frm);
	if (delay) frm._nasiya_preview_timer = setTimeout(run, delay);
	else run();
}

function maybe_generate_schedule(frm) {
	const d = frm.doc;
	if (!d.principal_amount || !d.number_of_installments || !d.start_date) {
		render_payment_preview(frm, null);
		return;
	}
	frappe.call({
		method:
			"nasiya365.nasiya365.doctype.installment_plan.installment_plan.calculate_installment_preview",
		args: {
			principal: d.principal_amount,
			down_payment: d.down_payment || 0,
			interest_rate: d.interest_rate || 0,
			num_installments: d.number_of_installments,
			frequency: d.frequency || "Ежемесячно",
			start_date: d.start_date,
		},
		callback(r) {
			if (!r.message) return;
			const preview = r.message;
			render_payment_preview(frm, preview);

			frm.set_value("financed_amount", preview.financed_amount);
			frm.set_value("total_interest", preview.total_interest);
			frm.set_value("total_amount", preview.total_amount);
			frm.set_value("installment_amount", preview.installment_amount);
			frm.set_value("end_date", preview.end_date);
			frm.set_value("remaining_balance", preview.total_amount - flt(d.paid_amount || 0));

			frm.clear_table("schedule");
			(preview.schedule || ([])).forEach((row) => {
				const child = frm.add_child("schedule");
				child.installment_number = row.installment_number;
				child.due_date = row.due_date;
				child.amount = row.amount;
				child.status = "Ожидает";
				child.paid_amount = 0;
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
	frm.add_custom_button(
		__("Отправить клиенту"),
		() =>
			frappe.show_alert({
				message: __("Скоро: отправка договора по SMS / email."),
				indicator: "orange",
			}),
		__("BNPL")
	);
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
	schedule_preview_refresh(frm, 0);
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
