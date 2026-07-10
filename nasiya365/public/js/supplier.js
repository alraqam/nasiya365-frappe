frappe.ui.form.on("Supplier", {
	refresh(frm) {
		show_supplier_balance(frm);
	},
});

function show_supplier_balance(frm) {
	if (frm.is_new()) return;
	frappe.call({
		method: "nasiya365.nasiya365.doctype.supplier.supplier.get_supplier_balance",
		args: { supplier: frm.doc.name },
		callback: (r) => {
			const balance = flt(r.message && r.message.balance_due || 0);
			const color = balance > 0.001 ? "orange" : "green";
			frm.set_intro(__("Долг поставщику: {0} USD", [balance.toFixed(2)]), color);
		},
	});
}
