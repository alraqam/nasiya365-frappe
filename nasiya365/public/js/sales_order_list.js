frappe.listview_settings["Sales Order"] = {
	add_fields: ["balance_amount", "status"],
	get_indicator(doc) {
		const open = (flt(doc.balance_amount) || 0) > 0;
		if (open) {
			return [__("Не оплачен"), "orange", "balance_amount,>,0"];
		}
		return [__("Оплачен"), "green", "balance_amount,<=,0"];
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
