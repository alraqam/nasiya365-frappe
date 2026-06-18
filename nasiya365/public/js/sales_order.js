frappe.ui.form.on("Sales Order", {
	refresh(frm) {
		// Trade-in entry: only meaningful for draft, saved SOs (need a name to link from).
		if (frm.doc.docstatus === 0 && !frm.is_new() && !frm.doc.trade_in) {
			frm.add_custom_button(__("Принять обмен"), () => so_open_trade_in(frm));
		}
		if (frm.doc.trade_in) {
			frm.add_custom_button(__("Открыть обмен"), () => {
				frappe.set_route("Form", "Trade In", frm.doc.trade_in);
			});
		}
		so_set_stock_entry_filter(frm);
		show_stock_hint(frm);
		so_apply_payment_method_options(frm);
		// New SO opens with one empty starter row; drop it so the grid starts clean.
		if (frm.is_new()) so_remove_blank_item_rows(frm);
		so_update_split_total_hint(frm);
	},
	warehouse(frm) {
		show_stock_hint(frm);
	},
	items_on_form_rendered(frm) {
		show_stock_hint(frm);
	},
	stock_entry(frm) {
		if (!frm.doc.stock_entry) return;
		// Keep the picked value visible in the field; user can pick a different
		// receipt afterwards by selecting again (overwrites this value).
		so_fetch_stock_entry_items(frm, frm.doc.stock_entry);
	},
	payment_method(frm) {
		so_on_payment_method_change(frm);
	},
	paid_amount(frm) {
		so_update_paid_amount_uzs(frm);
		so_update_split_total_hint(frm);
	},
	exchange_rate(frm) {
		so_update_paid_amount_uzs(frm);
	},
	payment_lines_add(frm) {
		so_update_split_total_hint(frm);
	},
	payment_lines_remove(frm) {
		so_update_split_total_hint(frm);
	},
});

frappe.ui.form.on("Sales Order Payment", {
	payment_method(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (row.payment_method === "Наличные UZS" && !row.exchange_rate) {
			frappe.call({
				method: "nasiya365.nasiya365.doctype.cashbox.cashbox.get_master_cashbox",
				args: { branch: frm.doc.branch || null },
				callback(r) {
					if (!r.message) return;
					frappe.db.get_value("Cashbox", r.message, "default_exchange_rate", (v) => {
						const rate = flt(v && v.default_exchange_rate);
						if (rate > 0) frappe.model.set_value(cdt, cdn, "exchange_rate", rate);
					});
				},
			});
		}
		so_update_split_total_hint(frm);
	},
	amount(frm, cdt, cdn) {
		so_recalc_uzs_row(cdt, cdn);
		so_update_split_total_hint(frm);
	},
	exchange_rate(frm, cdt, cdn) {
		so_recalc_uzs_row(cdt, cdn);
	},
});

function so_apply_payment_method_options(frm) {
	frappe.call({
		method: "nasiya365.api.bnpl_dashboard.get_payment_methods",
		callback(r) {
			if (!r.message || !r.message.length) return;
			const opts = r.message.join("\n");
			frm.set_df_property("payment_method", "options", opts);
			frm.refresh_field("payment_method");
			// Apply to child table rows too
			frm.set_df_property("payment_lines", "payment_method", "options", opts);
		},
	});
}

function so_on_payment_method_change(frm) {
	so_update_paid_amount_uzs(frm);
	if (frm.doc.payment_method !== "Наличные UZS" || flt(frm.doc.exchange_rate)) return;
	// Pre-fill the exchange rate from the branch's open cashbox.
	frappe.call({
		method: "nasiya365.nasiya365.doctype.cashbox.cashbox.get_master_cashbox",
		args: { branch: frm.doc.branch || null },
		callback(r) {
			if (!r.message) return;
			frappe.db.get_value("Cashbox", r.message, "default_exchange_rate", (v) => {
				const rate = flt(v && v.default_exchange_rate);
				if (rate > 0) {
					frm.set_value("exchange_rate", rate);
				}
			});
		},
	});
}

function so_update_paid_amount_uzs(frm) {
	if (frm.doc.payment_method !== "Наличные UZS") {
		frm.set_value("paid_amount_uzs", 0);
		return;
	}
	frm.set_value("paid_amount_uzs", flt(frm.doc.paid_amount) * flt(frm.doc.exchange_rate));
}

function so_set_stock_entry_filter(frm) {
	frm.set_query("stock_entry", () => ({
		query: "nasiya365.api.bnpl_dashboard.installment_plan_stock_entry_query",
		filters: { installment_plan: "" },
	}));
}

function so_fetch_stock_entry_items(frm, stock_entry) {
	frappe.call({
		method: "nasiya365.api.bnpl_dashboard.get_stock_entry_details_for_installment_plan",
		args: { stock_entry, installment_plan: "", sales_order: frm.is_new() ? "" : frm.doc.name },
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

function so_open_trade_in(frm) {
	// Open a new Trade In form pre-filled with customer + linked_sales_order + branch.
	frappe.route_options = {
		customer: frm.doc.customer,
		branch: frm.doc.branch,
		warehouse: frm.doc.warehouse,
		linked_sales_order: frm.doc.name,
		payout_method: "В счёт покупки",
	};
	frappe.new_doc("Trade In");
}

function so_remove_blank_item_rows(frm) {
	// Frappe auto-adds one empty row for the reqd `items` table on new docs
	// (see create_new.js). Drop rows with no product so the grid starts clean and
	// picked items don't leave a stray blank behind. Remove directly from the doc
	// array — GridRow.remove() no-ops at the refresh event because the grid rows
	// aren't registered in grid_rows_by_docname yet.
	const items = frm.doc.items || [];
	const blanks = items.filter((row) => !row.product);
	if (!blanks.length) return;
	blanks.forEach((row) => frappe.model.clear_doc(row.doctype, row.name));
	frm.doc.items = items.filter((row) => row.product);
	frm.doc.items.forEach((row, i) => (row.idx = i + 1));
	frm.refresh_field("items");
}

function so_apply_stock_item(frm, item) {
	// Fetch the selling price from the Product master (stock entry rate is cost, not selling price).
	frappe.call({
		method: "nasiya365.nasiya365.doctype.sales_order.sales_order.get_product_for_wizard",
		args: { product: item.product },
		callback(r) {
			so_remove_blank_item_rows(frm);
			const product_info = r.message || {};
			const selling_price = flt(product_info.selling_price || item.amount || 0);
			const child = frm.add_child("items");
			// Assign directly (synchronous) so `product` is on the row before any
			// grid refresh or re-run of so_remove_blank_item_rows that frappe.model
			// .set_value's async change events can trigger via frm.refresh().
			// Stock entry item values take priority (per-unit); fall back to the
			// Product master when the stock entry row left them blank.
			child.product = item.product;
			child.product_name = item.product_name || product_info.product_name || "";
			child.imei = item.imei || "";
			child.color = item.color || product_info.color || "";
			child.storage = item.storage || product_info.storage || "";
			child.condition = item.condition || product_info.condition || "";
			child.unit_price = selling_price;
			child.quantity = 1;
			frm.refresh_field("items");
			frappe.show_alert({
				message: __("Добавлено в Товары: {0}", [item.product_name || item.product]),
				indicator: "green",
			});
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

function so_recalc_uzs_row(cdt, cdn) {
	const row = locals[cdt][cdn];
	if (row.payment_method === "Наличные UZS" && row.exchange_rate) {
		frappe.model.set_value(cdt, cdn, "amount_uzs", flt(row.amount) * flt(row.exchange_rate));
	} else {
		frappe.model.set_value(cdt, cdn, "amount_uzs", 0);
	}
}

function so_update_split_total_hint(frm) {
	const lines = (frm.doc.payment_lines || []).filter(r => flt(r.amount) > 0);
	if (!lines.length) return;
	const split_total = lines.reduce((s, r) => s + flt(r.amount), 0);
	const order_total = flt(frm.doc.total_amount);
	const diff = order_total - split_total;
	const color = Math.abs(diff) < 0.01 ? "green" : "orange";
	const msg = diff > 0.01
		? __("Сумма способов оплаты: {0} USD. Не хватает: {1} USD", [split_total.toFixed(2), diff.toFixed(2)])
		: diff < -0.01
		? __("Сумма способов оплаты: {0} USD. Превышение: {1} USD", [split_total.toFixed(2), Math.abs(diff).toFixed(2)])
		: __("Сумма способов оплаты: {0} USD ✓", [split_total.toFixed(2)]);
	frm.dashboard.set_headline_alert(msg, color);
}
