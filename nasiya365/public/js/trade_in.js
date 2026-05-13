frappe.ui.form.on("Trade In", {
	onload(frm) {
		// Filter linked_sales_order: only draft SOs for the same customer.
		frm.set_query("linked_sales_order", () => ({
			filters: {
				docstatus: 0,
				customer: frm.doc.customer || "__none__",
			},
		}));
		// Warehouse filter follows the picked branch when possible.
		frm.set_query("warehouse", () => {
			const filters = {};
			if (frm.doc.branch) filters.branch = frm.doc.branch;
			return { filters };
		});
	},

	refresh(frm) {
		if (frm.doc.docstatus === 1 && frm.doc.stock_entry) {
			frm.add_custom_button(__("Открыть поступление"), () => {
				frappe.set_route("Form", "Stock Entry", frm.doc.stock_entry);
			});
		}
		if (frm.doc.docstatus === 1 && frm.doc.payout_method === "В счёт покупки" && frm.doc.linked_sales_order) {
			frm.add_custom_button(__("Открыть заказ"), () => {
				frappe.set_route("Form", "Sales Order", frm.doc.linked_sales_order);
			});
		}
	},

	customer(frm) {
		// Reset SO filter when customer changes.
		if (frm.doc.linked_sales_order) {
			frappe.db.get_value("Sales Order", frm.doc.linked_sales_order, "customer").then((r) => {
				if (r.message && r.message.customer !== frm.doc.customer) {
					frm.set_value("linked_sales_order", "");
				}
			});
		}
	},

	payout_method(frm) {
		const m = (frm.doc.payout_method || "").trim();
		if (m !== "Наличные") {
			frm.set_value("payout_currency", "");
		} else if (!frm.doc.payout_currency) {
			frm.set_value("payout_currency", "USD");
		}
		if (m !== "В счёт покупки") {
			frm.set_value("linked_sales_order", "");
		}
	},
});
