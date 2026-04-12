frappe.ui.form.on("Cashbox", {
	refresh(frm) {
		if (!frm.is_new() && frm.doc.status === "Открыта" && !frm.doc.is_master) {
			frm.add_custom_button(__("Закрыть и сдать"), () => {
				_open_handover_dialog(frm);
			}, __("Действия"));
		}
		if (!frm.is_new()) {
			frm.add_custom_button(__("Сверка"), () => {
				_open_reconciliation_dialog(frm);
			}, __("Действия"));
		}
	},
});

function _open_handover_dialog(frm) {
	// Pre-fetch the master cashbox for the branch, then show dialog
	frappe.call({
		method: "nasiya365.nasiya365.doctype.cashbox.cashbox.get_master_cashbox",
		args: { branch: frm.doc.branch || "" },
		callback(r) {
			const default_master = r.message || "";
			_show_handover_dialog(frm, default_master);
		},
	});
}

function _show_handover_dialog(frm, default_master) {
	// Build preview lines from the cashbox transactions in memory
	const income = {};
	(frm.doc.transactions || []).forEach((t) => {
		if (t.transaction_type !== "Приход") return;
		const key = `${t.payment_method || "Другое"} (${t.currency || "USD"})`;
		income[key] = (income[key] || 0) + flt(t.amount);
	});

	let preview_html = "";
	const lines = Object.entries(income).filter(([, v]) => v > 0);
	if (lines.length) {
		const rows = lines
			.map(([k, v]) => `<tr><td>${frappe.utils.escape_html(k)}</td><td style="text-align:right"><b>${v.toFixed(2)}</b></td></tr>`)
			.join("");
		preview_html = `<table class="table table-condensed table-bordered" style="margin:8px 0 0;font-size:13px;">
			<thead><tr><th>${__("Метод")}</th><th>${__("Сумма")}</th></tr></thead>
			<tbody>${rows}</tbody>
		</table>`;
	} else {
		preview_html = `<p class="text-muted">${__("Нет приходных транзакций")}</p>`;
	}

	const d = new frappe.ui.Dialog({
		title: __("Закрыть кассу и сдать инкассацию"),
		fields: [
			{
				fieldtype: "HTML",
				fieldname: "preview",
				options: `<div style="margin-bottom:8px;font-weight:600">${__("Сумма к передаче:")}</div>${preview_html}`,
			},
			{ fieldtype: "Section Break" },
			{
				fieldtype: "Link",
				fieldname: "to_cashbox",
				label: __("Главная касса (менеджер)"),
				options: "Cashbox",
				default: default_master,
				get_query: () => ({ filters: { is_master: 1, status: "Открыта" } }),
				reqd: 1,
			},
		],
		primary_action_label: __("Закрыть и создать инкассацию"),
		primary_action(vals) {
			d.hide();
			frappe.call({
				method: "nasiya365.nasiya365.doctype.cashbox.cashbox.close_and_transfer",
				args: {
					cashbox_name: frm.doc.name,
					to_cashbox: vals.to_cashbox,
				},
				freeze: true,
				freeze_message: __("Закрываем кассу..."),
				callback(r) {
					if (r.exc) return;
					frappe.show_alert({
						message: __("Касса закрыта. Инкассация создана: ") + r.message,
						indicator: "green",
					});
					frm.reload_doc();
					frappe.set_route("Form", "Cash Handover", r.message);
				},
			});
		},
	});
	d.show();
}

function _open_reconciliation_dialog(frm) {
	const d = new frappe.ui.Dialog({
		title: __("Сверка кассы"),
		fields: [
			{
				fieldtype: "Date",
				fieldname: "recon_date",
				label: __("Дата"),
				default: frappe.datetime.get_today(),
				reqd: 1,
			},
			{ fieldtype: "Section Break" },
			{ fieldtype: "HTML", fieldname: "recon_result", options: "" },
		],
		primary_action_label: __("Проверить"),
		primary_action(vals) {
			frappe.call({
				method: "nasiya365.api.bnpl_dashboard.get_cashbox_reconciliation",
				args: { cashbox: frm.doc.name, date: vals.recon_date },
				freeze: true,
				freeze_message: __("Сверяем..."),
				callback(r) {
					const data = r.message || {};
					const ledger = data.ledger_totals || {};
					const payments = data.payment_totals || {};
					const discs = data.discrepancies || [];
					const allMethods = [...new Set([...Object.keys(ledger), ...Object.keys(payments)])].sort();

					let rows = allMethods.map((m) => {
						const l = (ledger[m] || 0).toFixed(2);
						const p = (payments[m] || 0).toFixed(2);
						const diff = ((ledger[m] || 0) - (payments[m] || 0)).toFixed(2);
						const cls = Math.abs(parseFloat(diff)) > 0.01 ? "text-danger" : "text-success";
						return `<tr>
							<td>${frappe.utils.escape_html(m)}</td>
							<td style="text-align:right">${l}</td>
							<td style="text-align:right">${p}</td>
							<td style="text-align:right" class="${cls}"><b>${diff}</b></td>
						</tr>`;
					}).join("");

					const badge = data.is_balanced
						? `<span class="indicator-pill green">${__("Сходится")}</span>`
						: `<span class="indicator-pill red">${__("Расхождение")} (${discs.length})</span>`;

					const html = `
						<div style="margin-bottom:8px">${badge}</div>
						<table class="table table-condensed table-bordered" style="font-size:13px">
							<thead><tr>
								<th>${__("Метод")}</th>
								<th style="text-align:right">${__("Касса (лист.)")}</th>
								<th style="text-align:right">${__("Платежи")}</th>
								<th style="text-align:right">${__("Разница")}</th>
							</tr></thead>
							<tbody>${rows || `<tr><td colspan="4" class="text-muted text-center">${__("Нет данных")}</td></tr>`}</tbody>
						</table>`;

					d.fields_dict.recon_result.$wrapper.html(html);
				},
			});
		},
	});
	d.show();
}
