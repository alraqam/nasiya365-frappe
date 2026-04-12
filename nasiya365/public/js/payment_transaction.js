frappe.ui.form.on("Payment Transaction", {
	refresh(frm) {
		if (frm.fields_dict.late_fee_applied) {
			frm.set_df_property("late_fee_applied", "hidden", 1);
		}
		ensure_payment_form_css();
		render_installment_plans(frm);
		recalc_payment_table_total(frm);
		frm.add_custom_button(__("Сдача"), () => open_change_calculator(frm), __("Касса"));
	},
	exchange_rate(frm) {
		recalc_payment_table_total(frm);
	},
	reference_doctype(frm) {
		refresh_plan_panel(frm);
	},
	reference_name(frm) {
		refresh_plan_panel(frm);
	},
	customer(frm) {
		render_installment_plans(frm);
	},
	payment_lines_add(frm, cdt, cdn) {
		const row = frappe.get_doc(cdt, cdn);
		if (!row.exchange_rate && flt(frm.doc.exchange_rate) > 0) {
			frappe.model.set_value(cdt, cdn, "exchange_rate", flt(frm.doc.exchange_rate));
		}
		recalc_payment_table_total(frm);
	},
	payment_lines_remove(frm) {
		recalc_payment_table_total(frm);
	},
});

function ensure_payment_form_css() {
	const id = "nasiya-payment-form-css";
	if (document.getElementById(id)) return;
	const css = `
		.form-layout .section-head {
			margin-top: 8px;
			margin-bottom: 8px;
		}
		.layout-main-section .form-column .frappe-control {
			margin-bottom: 8px;
		}
		[data-fieldname="payment_lines"] .grid-heading-row {
			background: var(--subtle-fg);
		}
		[data-fieldname="payment_lines"] .grid-body {
			max-height: 180px;
			min-height: 90px;
		}
		[data-fieldname="payment_lines"] .grid-static-col,
		[data-fieldname="payment_lines"] .grid-row-check {
			width: 32px;
		}
		[data-fieldname="payment_lines"] .grid-heading-row .grid-row > div {
			font-size: 12px;
		}
		[data-fieldname="payment_lines"] .rows {
			font-size: 13px;
		}
		[data-fieldname="summary_section"] .form-column:first-child {
			max-width: 560px;
		}
	`;
	const el = document.createElement("style");
	el.id = id;
	el.textContent = css;
	document.head.appendChild(el);
}

frappe.ui.form.on("Payment Transaction Line", {
	amount(frm) {
		recalc_payment_table_total(frm);
	},
	currency(frm) {
		recalc_payment_table_total(frm);
	},
	exchange_rate(frm) {
		recalc_payment_table_total(frm);
	},
	payment_method(frm) {
		recalc_payment_table_total(frm);
	},
});

function recalc_payment_table_total(frm) {
	const lines = frm.doc.payment_lines || [];
	if (!lines.length) {
		frm.set_value("amount", 0);
		frm.set_value("payment_method", "");
		return;
	}
	const defaultRate = flt(frm.doc.exchange_rate);
	let total = 0;
	const methods = new Set();

	for (const row of lines) {
		const amount = flt(row.amount);
		if (!amount) continue;
		const currency = (row.currency || "USD").toUpperCase();
		if (currency === "UZS") {
			const rate = flt(row.exchange_rate || defaultRate);
			if (rate <= 0) continue;
			total += amount / rate;
		} else {
			total += amount;
		}
		if (row.payment_method) methods.add(row.payment_method);
	}

	frm.set_value("amount", total);

	if (methods.size > 1) {
		frm.set_value("payment_method", "Комбинированный");
	} else if (methods.size === 1) {
		frm.set_value("payment_method", Array.from(methods)[0]);
	}

	// Warn cashier if payment exceeds plan remaining balance
	const rd = (frm.doc.reference_doctype || "").trim();
	const rn = (frm.doc.reference_name || "").trim();
	if (rd === "Installment Plan" && rn && total > 0) {
		frappe.db.get_value("Installment Plan", rn, "remaining_balance", (r) => {
			const remaining = flt(r && r.remaining_balance);
			if (remaining > 0 && total > remaining + 0.001) {
				const excess = (total - remaining).toFixed(2);
				frappe.show_alert({
					message: __("Переплата: {0} USD сверх остатка по плану ({1} USD)", [excess, remaining.toFixed(2)]),
					indicator: "orange",
				}, 8);
			}
		});
	}
}

function open_change_calculator(frm) {
	const due = flt(frm.doc.amount);
	const d = new frappe.ui.Dialog({
		title: __("Сдача"),
		fields: [
			{
				fieldname: "hint",
				fieldtype: "HTML",
				options: `<p class="text-muted small">${__("К оплате по документу")}: <strong>${format_currency(
					due
				)}</strong></p>`,
			},
			{
				fieldname: "tender",
				fieldtype: "Currency",
				label: __("Сумма от клиента"),
			},
		],
		primary_action_label: __("Рассчитать"),
		primary_action(values) {
			const tender = flt(values.tender);
			const ch = tender - due;
			if (ch >= 0) {
				frappe.msgprint({
					title: __("Сдача"),
					message: `${__("Сдача")}: ${format_currency(ch)}`,
					indicator: "green",
				});
			} else {
				frappe.msgprint({
					title: __("Недостаточно"),
					message: `${__("Не хватает")}: ${format_currency(-ch)}`,
					indicator: "orange",
				});
			}
			d.hide();
		},
	});
	d.show();
}

function refresh_plan_panel(frm) {
	if (frm._nasiya_payment_plans_cache && frm.doc.customer) {
		paint_installment_plans(frm, frm._nasiya_payment_plans_cache);
	} else {
		render_installment_plans(frm);
	}
}

function render_installment_plans(frm) {
	const wrapper = frm.fields_dict.installment_plans_info?.$wrapper;
	if (!wrapper) return;

	const customer = frm.doc.customer;
	if (!customer) {
		frm._nasiya_payment_plans_cache = null;
		wrapper.html('<div class="text-muted">Выберите клиента, чтобы увидеть его планы рассрочки.</div>');
		return;
	}

	wrapper.html('<div class="text-muted">Загрузка планов рассрочки...</div>');
	frappe.call({
		method: "nasiya365.nasiya365.doctype.payment_transaction.payment_transaction.get_customer_installment_plans",
		args: { customer },
		callback: (r) => {
			frm._nasiya_payment_plans_cache = r.message || [];
			paint_installment_plans(frm, frm._nasiya_payment_plans_cache);
		},
	});
}

function ensure_nasiya_payment_plans_css() {
	const id = "nasiya-payment-plans-css";
	const css = `
		[data-fieldname="installment_plans_info"] .frappe-control,
		[data-fieldname="installment_plans_info"] .control-value {
			max-width: 100%;
		}
		.nasiya-payment-plans-wrap {
			width: 100%;
			max-width: 100%;
			overflow-x: visible;
			min-width: 0;
		}
		.nasiya-payment-plans {
			table-layout: fixed;
			width: 100%;
			margin-bottom: 0;
			font-size: inherit;
		}
		.nasiya-payment-plans th,
		.nasiya-payment-plans td {
			word-break: break-word;
			overflow-wrap: anywhere;
			vertical-align: middle;
			padding: var(--padding-sm, 0.5rem) var(--padding-md, 0.75rem);
			font-size: inherit;
			line-height: inherit;
		}
		.nasiya-payment-plans td.nasiya-pay-action,
		.nasiya-payment-plans th.nasiya-pay-action {
			width: 5.5rem;
			white-space: nowrap;
			text-align: center;
		}
		.nasiya-payment-plans .use-plan {
			font-size: inherit;
		}
	`;
	let el = document.getElementById(id);
	if (!el) {
		el = document.createElement("style");
		el.id = id;
		document.head.appendChild(el);
	}
	el.textContent = css;
}

function paint_installment_plans(frm, plans) {
	ensure_nasiya_payment_plans_css();
	const wrapper = frm.fields_dict.installment_plans_info?.$wrapper;
	if (!wrapper) return;

	if (!plans.length) {
		wrapper.html('<div class="text-muted">У этого клиента нет планов рассрочки.</div>');
		return;
	}

	const refOk =
		frm.doc.reference_doctype === "Installment Plan" &&
		(frm.doc.reference_name || "").toString().trim();
	const selectedPlanMeta = refOk ? plans.find((x) => x.name === frm.doc.reference_name) : null;

	const contractLabel =
		(selectedPlanMeta && (selectedPlanMeta.contract_number || selectedPlanMeta.name)) ||
		frm.doc.reference_name ||
		"";

	const selectionBanner = refOk
		? `<div class="alert alert-info nasiya-pay-selection" style="margin-bottom:10px;">
				<strong>Платёж привязан к:</strong>
				${frappe.utils.escape_html(contractLabel)}
				<span class="text-muted"> · план </span>
				<a href="/app/installment-plan/${encodeURIComponent(frm.doc.reference_name)}" target="_blank">${frappe.utils.escape_html(frm.doc.reference_name)}</a>
		   </div>`
		: `<div class="alert alert-warning nasiya-pay-selection" style="margin-bottom:10px;">
				<strong>Договор не выбран.</strong> Нажмите «Выбрать» в строке нужного плана.
		   </div>`;

	const rows = plans
		.map((p) => {
			const debt = frappe.format(p.remaining_balance || 0, { fieldtype: "Currency" });
			const device = frappe.utils.escape_html(p.device_name || "-");
			const imei = p.imei ? ` / IMEI: ${frappe.utils.escape_html(p.imei)}` : "";
			const status = frappe.utils.escape_html(p.contract_status || p.status || "");
			const disabled = (p.remaining_balance || 0) <= 0 ? "disabled" : "";
			const isSelected = refOk && p.name === frm.doc.reference_name;
			const rowClass = isSelected ? ' class="table-info"' : "";
			const dogovor = (p.contract_number || p.name || "-").toString().trim();
			const actionCell = isSelected
				? `<td class="nasiya-pay-action"></td>`
				: `<td class="nasiya-pay-action"><button type="button" class="btn btn-primary btn-sm use-plan" data-plan="${frappe.utils.escape_html(p.name)}" ${disabled}>${__("Выбрать")}</button></td>`;
			return `
				<tr${rowClass}>
					<td><a href="/app/installment-plan/${encodeURIComponent(p.name)}" target="_blank">${frappe.utils.escape_html(p.name)}</a></td>
					<td>${frappe.utils.escape_html(dogovor)}</td>
					<td>${device}${imei}</td>
					<td>${debt}</td>
					<td>${status}</td>
					${actionCell}
				</tr>
			`;
		})
		.join("");

	wrapper.html(`
		<div>
			${selectionBanner}
			<div class="text-muted" style="margin-bottom: 8px;">Планы рассрочки клиента:</div>
			<div class="nasiya-payment-plans-wrap">
				<table class="table table-bordered nasiya-payment-plans">
					<colgroup>
						<col style="width:15%" />
						<col style="width:14%" />
						<col style="width:30%" />
						<col style="width:14%" />
						<col style="width:14%" />
						<col style="width:13%" />
					</colgroup>
					<thead>
						<tr>
							<th>План</th>
							<th>Договор</th>
							<th>Устройство</th>
							<th>Остаток</th>
							<th>Статус</th>
							<th class="nasiya-pay-action">Выбор</th>
						</tr>
					</thead>
					<tbody>${rows}</tbody>
				</table>
			</div>
		</div>
	`);

	wrapper.find(".use-plan").on("click", function () {
		const planName = $(this).data("plan");
		const plan = plans.find((x) => x.name === planName) || {};
		select_installment_plan_for_payment(frm, plan, plans);
	});
}

function select_installment_plan_for_payment(frm, plan, plans) {
	const suggested = Math.max(
		0,
		Math.min(flt(plan.installment_amount || 0), flt(plan.remaining_balance || 0))
	);
	frappe.model.set_value(frm.doc.doctype, frm.doc.name, "reference_doctype", "Installment Plan");
	frappe.model.set_value(frm.doc.doctype, frm.doc.name, "reference_name", plan.name);
	if (!flt(frm.doc.amount) && suggested > 0) {
		frappe.model.set_value(frm.doc.doctype, frm.doc.name, "amount", suggested);
	}
	frm.refresh_fields(["reference_doctype", "reference_name", "amount"]);
	frm._nasiya_payment_plans_cache = plans;
	paint_installment_plans(frm, plans);
}
