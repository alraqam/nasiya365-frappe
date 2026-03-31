frappe.ui.form.on("Payment Transaction", {
	refresh(frm) {
		render_installment_plans(frm);
	},
	customer(frm) {
		render_installment_plans(frm);
	},
});

function render_installment_plans(frm) {
	const wrapper = frm.fields_dict.installment_plans_info?.$wrapper;
	if (!wrapper) return;

	const customer = frm.doc.customer;
	if (!customer) {
		wrapper.html('<div class="text-muted">Выберите клиента, чтобы увидеть его планы рассрочки.</div>');
		return;
	}

	wrapper.html('<div class="text-muted">Загрузка планов рассрочки...</div>');
	frappe.call({
		method: "nasiya365.nasiya365.doctype.payment_transaction.payment_transaction.get_customer_installment_plans",
		args: { customer },
		callback: (r) => {
			const plans = r.message || [];
			if (!plans.length) {
				wrapper.html('<div class="text-muted">У этого клиента нет планов рассрочки.</div>');
				return;
			}

			const rows = plans
				.map((p) => {
					const debt = frappe.format(p.remaining_balance || 0, { fieldtype: "Currency" });
					const device = frappe.utils.escape_html(p.device_name || "-");
					const imei = p.imei ? ` / IMEI: ${frappe.utils.escape_html(p.imei)}` : "";
					const status = frappe.utils.escape_html(p.contract_status || p.status || "");
					const disabled = (p.remaining_balance || 0) <= 0 ? "disabled" : "";
					return `
						<tr>
							<td><a href="/app/installment-plan/${encodeURIComponent(p.name)}" target="_blank">${frappe.utils.escape_html(p.name)}</a></td>
							<td>${device}${imei}</td>
							<td>${debt}</td>
							<td>${status}</td>
							<td><button class="btn btn-xs btn-primary use-plan" data-plan="${frappe.utils.escape_html(p.name)}" ${disabled}>Выбрать</button></td>
						</tr>
					`;
				})
				.join("");

			wrapper.html(`
				<div>
					<div class="text-muted" style="margin-bottom: 8px;">Планы рассрочки клиента:</div>
					<div class="table-responsive">
						<table class="table table-bordered table-sm">
							<thead>
								<tr>
									<th>План</th>
									<th>Устройство</th>
									<th>Остаток долга</th>
									<th>Статус</th>
									<th>Действие</th>
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
				const suggested = Math.max(
					0,
					Math.min(
						flt(plan.installment_amount || 0),
						flt(plan.remaining_balance || 0)
					)
				);
				frm.set_value("reference_doctype", "Installment Plan");
				frm.set_value("reference_name", planName);
				// Auto-suggest amount from selected plan unless user has already entered one.
				if (!flt(frm.doc.amount) && suggested > 0) {
					frm.set_value("amount", suggested);
				}
			});
		},
	});
}
