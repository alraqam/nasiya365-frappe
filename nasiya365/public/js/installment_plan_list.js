frappe.listview_settings["Installment Plan"] = {
	get_indicator(doc) {
		const map = {
			"Завершен":  [__("Завершен"),  "green",  "status,=,Завершен"],
			"Активный":  [__("Активный"),  "blue",   "status,=,Активный"],
			"Просрочен": [__("Просрочен"), "red",    "status,=,Просрочен"],
			"Черновик":  [__("Черновик"),  "grey",   "status,=,Черновик"],
			"Списан":    [__("Списан"),    "orange", "status,=,Списан"],
		};
		return map[doc.status] || [__(doc.status), "grey", `status,=,${doc.status}`];
	},
};
