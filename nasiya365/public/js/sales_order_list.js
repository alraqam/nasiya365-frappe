frappe.listview_settings["Sales Order"] = {
	add_fields: ["order_kind", "balance_amount", "status"],
	get_indicator(doc) {
		// All sales are installment-based; distinguish outstanding vs no remaining balance on the order.
		const open = (flt(doc.balance_amount) || 0) > 0;
		if (open || doc.order_kind === "Rassrochka") {
			return [__("В рассрочке"), "orange", "balance_amount,>,0"];
		}
		return [__("Погашено"), "green", "balance_amount,<=,0"];
	},
	onload(listview) {
		// Sales orders are loaded via Data Import Tool, not the desk list primary action.
		listview.page.set_primary_action(__("Инструмент импорта"), () =>
			frappe.set_route("List", "Data Import Tool"),
		);
		listview.page.add_inner_button(__("Планы рассрочки"), () => {
			frappe.set_route("List", "Installment Plan");
		});
	},
};
