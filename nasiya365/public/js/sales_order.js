frappe.ui.form.on("Sales Order", {
	refresh(frm) {
		if (frm.is_new()) {
			frm.add_custom_button(__("Оформить в рассрочку"), () => new NasiyaSalesWizard().start()).addClass("btn-primary");
		}
		so_set_stock_entry_filter(frm);
		show_stock_hint(frm);
	},
	warehouse(frm) {
		show_stock_hint(frm);
	},
	items_on_form_rendered(frm) {
		show_stock_hint(frm);
	},
	stock_entry(frm) {
		if (!frm.doc.stock_entry) return;
		// Clear the field immediately so the user can pick again for the next item.
		const se_name = frm.doc.stock_entry;
		frm.set_value("stock_entry", "");
		so_fetch_stock_entry_items(frm, se_name);
	},
});

function so_set_stock_entry_filter(frm) {
	frm.set_query("stock_entry", () => ({
		query: "nasiya365.api.bnpl_dashboard.installment_plan_stock_entry_query",
		filters: { installment_plan: "" },
	}));
}

function so_fetch_stock_entry_items(frm, stock_entry) {
	frappe.call({
		method: "nasiya365.api.bnpl_dashboard.get_stock_entry_details_for_installment_plan",
		args: { stock_entry, installment_plan: "" },
		callback(r) {
			if (!r.message) return;
			const items = r.message.free_items || [];
			if (!items.length) {
				frappe.show_alert({ message: __("Нет доступных позиций в этом поступлении"), indicator: "orange" });
				return;
			}
			if (items.length === 1) {
				so_apply_stock_item(frm, items[0]);
			} else {
				so_pick_stock_item(frm, items);
			}
		},
	});
}

function so_pick_stock_item(frm, items) {
	const rows = items.map((item, idx) => {
		const name = frappe.utils.escape_html(item.product_name || item.product || "—");
		const color = frappe.utils.escape_html(item.color || "—");
		const storage = frappe.utils.escape_html(item.storage || "—");
		const imei = frappe.utils.escape_html(item.imei || "—");
		const price = frappe.format(item.amount || 0, { fieldtype: "Currency" });
		return `<tr>
			<td>${name}</td>
			<td>${color}</td>
			<td>${storage}</td>
			<td><code>${imei}</code></td>
			<td>${price}</td>
			<td><button class="btn btn-primary btn-sm pick-item" data-idx="${idx}">${__("Добавить")}</button></td>
		</tr>`;
	}).join("");

	const html = `<table class="table table-bordered table-sm" style="margin-bottom:0">
		<thead><tr>
			<th>${__("Товар")}</th>
			<th>${__("Цвет")}</th>
			<th>${__("Память")}</th>
			<th>${__("IMEI")}</th>
			<th>${__("Цена")}</th>
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
		so_apply_stock_item(frm, items[idx]);
		$(this).closest("tr").addClass("table-success").find("button").prop("disabled", true).text(__("Добавлено"));
	});
	d.show();
}

function so_apply_stock_item(frm, item) {
	// Fetch the selling price from the Product master (stock entry rate is cost, not selling price).
	frappe.call({
		method: "nasiya365.nasiya365.doctype.sales_order.sales_order.get_product_for_wizard",
		args: { product: item.product },
		callback(r) {
			const selling_price = flt((r.message || {}).selling_price || item.amount || 0);
			const child = frm.add_child("items");
			frappe.model.set_value(child.doctype, child.name, "product", item.product);
			frappe.model.set_value(child.doctype, child.name, "imei", item.imei || "");
			frappe.model.set_value(child.doctype, child.name, "color", item.color || "");
			frappe.model.set_value(child.doctype, child.name, "storage", item.storage || "");
			frappe.model.set_value(child.doctype, child.name, "unit_price", selling_price);
			frappe.model.set_value(child.doctype, child.name, "quantity", 1);
			frm.refresh_field("items");
		},
	});
}

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
