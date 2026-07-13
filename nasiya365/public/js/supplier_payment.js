frappe.ui.form.on("Supplier Payment", {
	refresh(frm) {
		recalc_supplier_payment_total(frm);
	},
	exchange_rate(frm) {
		recalc_supplier_payment_total(frm);
	},
	payment_lines_add(frm, cdt, cdn) {
		const row = frappe.get_doc(cdt, cdn);
		if (!row.exchange_rate && flt(frm.doc.exchange_rate) > 0) {
			frappe.model.set_value(cdt, cdn, "exchange_rate", flt(frm.doc.exchange_rate));
		}
		recalc_supplier_payment_total(frm);
	},
	payment_lines_remove(frm) {
		recalc_supplier_payment_total(frm);
	},
});

frappe.ui.form.on("Supplier Payment Line", {
	amount(frm) {
		recalc_supplier_payment_total(frm);
	},
	currency(frm) {
		recalc_supplier_payment_total(frm);
	},
	exchange_rate(frm) {
		recalc_supplier_payment_total(frm);
	},
	payment_method(frm) {
		recalc_supplier_payment_total(frm);
	},
});

function recalc_supplier_payment_total(frm) {
	const lines = frm.doc.payment_lines || [];
	if (!lines.length) {
		frm.set_value("amount", 0);
		frm.set_value("payment_method", "");
		return;
	}
	const defaultRate = flt(frm.doc.exchange_rate);
	let total = 0;
	const methods = new Set();

	for (const row of lines) {
		const amount = flt(row.amount);
		if (!amount) continue;
		const currency = (row.currency || "USD").toUpperCase();
		if (currency === "UZS") {
			const rate = flt(row.exchange_rate || defaultRate);
			if (rate <= 0) continue;
			total += amount / rate;
		} else {
			total += amount;
		}
		if (row.payment_method) methods.add(row.payment_method);
	}

	frm.set_value("amount", total);

	if (methods.size > 1) {
		frm.set_value("payment_method", "Комбинированный");
	} else if (methods.size === 1) {
		frm.set_value("payment_method", Array.from(methods)[0]);
	}
}
