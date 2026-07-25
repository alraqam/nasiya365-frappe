frappe.listview_settings["Stock Entry"] = {
	onload(listview) {
		// IMEI lives on the child table (Stock Entry Item), so a standard filter
		// on the parent can't reach it. Offer a dedicated «Найти по IMEI» button
		// that applies a child-table filter (partial match, digits only).
		listview.page.add_inner_button(__("Найти по IMEI"), () => {
			const dialog = new frappe.ui.Dialog({
				title: __("Поиск прихода по IMEI"),
				fields: [
					{
						fieldname: "imei",
						label: __("IMEI"),
						fieldtype: "Data",
						reqd: 1,
						description: __("Частичный поиск, минимум 3 цифры"),
					},
				],
				primary_action_label: __("Найти"),
				primary_action(values) {
					const term = (values.imei || "").replace(/\D/g, "");
					if (term.length < 3) {
						frappe.msgprint(__("Введите минимум 3 цифры IMEI."));
						return;
					}
					listview.filter_area.add([["Stock Entry Item", "imei", "like", `%${term}%`]]);
					dialog.hide();
				},
			});
			dialog.show();
		});
	},
};
