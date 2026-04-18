frappe.ui.form.on("Sales Order", {
	refresh(frm) {
		if (frm.is_new()) {
			frm.add_custom_button(__("Оформить в рассрочку"), () => new NasiyaSalesWizard().start()).addClass("btn-primary");
		}
		show_stock_hint(frm);
	},
	warehouse(frm) {
		show_stock_hint(frm);
	},
	items_on_form_rendered(frm) {
		show_stock_hint(frm);
	},
});

function show_stock_hint(frm) {
	const item = (frm.doc.items || [])[0];
	if (!item || !item.product) return;
	frappe.call({
		method: "nasiya365.nasiya365.doctype.sales_order.sales_order.get_product_stock_available",
		args: { product: item.product, warehouse: frm.doc.warehouse || null },
		callback: (r) => {
			const available = flt(r.message?.available_qty || 0);
			const color = available > 0 ? "green" : "red";
			frm.set_intro(__("Остаток на складе по выбранному товару: {0}", [available]), color);
		},
	});
}

class NasiyaSalesWizard {
	constructor() {
		this.state = {
			customer: "",
			branch: "",
			product: "",
			price: 0,
			qty: 1,
			months: 6,
			down_payment: 0,
		};
	}

	start() {
		this.stepClient();
	}

	stepClient() {
		const d = new frappe.ui.Dialog({
			title: __("Шаг 1/4 · Клиент"),
			fields: [
				{ fieldname: "customer", label: __("Клиент"), fieldtype: "Link", options: "Customer Profile", reqd: 1, default: this.state.customer },
				{ fieldname: "branch", label: __("Филиал"), fieldtype: "Link", options: "Branch", reqd: 1, default: this.state.branch },
				{ fieldname: "risk", fieldtype: "HTML" },
			],
			primary_action_label: __("Далее"),
			primary_action: (values) => {
				this.state.customer = values.customer;
				this.state.branch = values.branch;
				d.hide();
				this.stepProduct();
			},
		});
		const updateRisk = () => {
			const customer = d.get_value("customer");
			if (!customer) return d.fields_dict.risk.$wrapper.html("");
			frappe.call({
				method: "nasiya365.api.bnpl_dashboard.get_client_risk_snapshot",
				args: { customer },
				callback: (r) => {
					const risk = r.message || {};
					d.fields_dict.risk.$wrapper.html(`
						<div class="small text-muted" style="margin-top:8px">
							Скоринг: <b>${risk.risk_score || 0}</b>,
							Активные займы: <b>${risk.active_loans || 0}</b>,
							Риск: <b>${risk.risk_level || "-"}</b>
						</div>
					`);
				},
			});
		};
		d.fields_dict.customer.$input.on("change", updateRisk);
		d.show();
		updateRisk();
	}

	stepProduct() {
		const d = new frappe.ui.Dialog({
			title: __("Шаг 2/4 · Товар"),
			fields: [
				{ fieldname: "product", label: __("Товар"), fieldtype: "Link", options: "Product", reqd: 1, default: this.state.product },
				{ fieldname: "qty", label: __("Количество"), fieldtype: "Int", reqd: 1, default: this.state.qty },
				{ fieldname: "price_info", fieldtype: "HTML" },
				{ fieldname: "stock_info", fieldtype: "HTML" },
			],
			primary_action_label: __("Далее"),
			secondary_action_label: __("Назад"),
			secondary_action: () => {
				d.hide();
				this.stepClient();
			},
			primary_action: (values) => {
				this.state.product = values.product;
				this.state.qty = values.qty || 1;
				d.hide();
				this.stepPlan();
			},
		});
		const updatePrice = () => {
			const product = d.get_value("product");
			if (!product) return;
			frappe.call({
				method: "nasiya365.nasiya365.doctype.sales_order.sales_order.get_product_for_wizard",
				args: { product },
				callback: (res) => {
					const data = res.message || {};
					this.state.price = Number(data.selling_price || 0);
					const qty = Number(d.get_value("qty") || 1);
					d.fields_dict.price_info.$wrapper.html(
						`<div class="small text-muted">Цена: <b>${format_currency(this.state.price)}</b> · Итого: <b>${format_currency(this.state.price * qty)}</b></div>`
					);
				},
			});
			frappe.call({
				method: "nasiya365.nasiya365.doctype.sales_order.sales_order.get_product_stock_available",
				args: { product, warehouse: this.state.warehouse || null },
				callback: (r) => {
					const available = flt(r.message?.available_qty || 0);
					const cls = available > 0 ? "text-success" : "text-danger";
					d.fields_dict.stock_info.$wrapper.html(
						`<div class="small ${cls}">Остаток на складе: <b>${available}</b></div>`
					);
				},
			});
		};
		d.fields_dict.product.$input.on("change", updatePrice);
		d.fields_dict.qty.$input.on("change", updatePrice);
		d.show();
		updatePrice();
	}

	stepPlan() {
		const d = new frappe.ui.Dialog({
			title: __("Шаг 3/4 · План"),
			fields: [
				{ fieldname: "months", label: __("Срок"), fieldtype: "Select", options: "3\n6\n12", reqd: 1, default: String(this.state.months) },
				{ fieldname: "down", label: __("Первоначальный взнос"), fieldtype: "Currency", default: this.state.down_payment },
				{ fieldname: "preview", fieldtype: "HTML" },
			],
			primary_action_label: __("Далее"),
			secondary_action_label: __("Назад"),
			secondary_action: () => {
				d.hide();
				this.stepProduct();
			},
			primary_action: (values) => {
				this.state.months = Number(values.months || 6);
				this.state.down_payment = Number(values.down || 0);
				d.hide();
				this.stepConfirm();
			},
		});
		const updatePreview = () => {
			const months = Number(d.get_value("months") || 6);
			const down = Number(d.get_value("down") || 0);
			const principal = this.state.price * this.state.qty;
			frappe.call({
				method: "nasiya365.nasiya365.doctype.installment_plan.installment_plan.calculate_installment_preview",
				args: {
					principal,
					down_payment: down,
					interest_rate: 5,
					num_installments: months,
					frequency: "Ежемесячно",
					start_date: frappe.datetime.get_today(),
				},
				callback: (r) => {
					const p = r.message || {};
					d.fields_dict.preview.$wrapper.html(`
						<div class="small text-muted">
							Ежемесячно: <b>${format_currency(p.installment_amount || 0)}</b><br/>
							Итого к возврату: <b>${format_currency(p.total_amount || 0)}</b><br/>
							Переплата: <b>${format_currency(p.total_interest || 0)}</b>
						</div>
					`);
				},
			});
		};
		d.fields_dict.months.$input.on("change", updatePreview);
		d.fields_dict.down.$input.on("change", updatePreview);
		d.show();
		updatePreview();
	}

	stepConfirm() {
		const total = this.state.price * this.state.qty;
		const d = new frappe.ui.Dialog({
			title: __("Шаг 4/4 · Подтверждение"),
			fields: [
				{
					fieldname: "summary",
					fieldtype: "HTML",
					options: `
						<div>
							<div><b>Клиент:</b> ${frappe.utils.escape_html(this.state.customer)}</div>
							<div><b>Филиал:</b> ${frappe.utils.escape_html(this.state.branch)}</div>
							<div><b>Товар:</b> ${frappe.utils.escape_html(this.state.product)}</div>
							<div><b>Срок:</b> ${this.state.months} мес.</div>
							<div><b>Сумма:</b> ${format_currency(total)}</div>
							<div><b>Первый взнос:</b> ${format_currency(this.state.down_payment)}</div>
						</div>
					`,
				},
			],
			primary_action_label: __("Оформить"),
			secondary_action_label: __("Назад"),
			secondary_action: () => {
				d.hide();
				this.stepPlan();
			},
			primary_action: () => {
				frappe.call({
					method: "nasiya365.api.bnpl_dashboard.create_sales_order_from_wizard",
					args: {
						payload: {
							customer: this.state.customer,
							branch: this.state.branch,
							product: this.state.product,
							price: this.state.price,
							qty: this.state.qty,
							months: this.state.months,
							down_payment: this.state.down_payment,
						},
					},
					callback: (r) => {
						d.hide();
						if (r.message && r.message.plan) {
							frappe.set_route("Form", "Installment Plan", r.message.plan);
						} else if (r.message && r.message.so) {
							frappe.set_route("Form", "Sales Order", r.message.so);
						}
					},
				});
			},
		});
		d.show();
	}
}
