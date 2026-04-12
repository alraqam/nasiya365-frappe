frappe.ui.form.on("Cash Handover Line", {
	confirmed_amount(frm, cdt, cdn) {
		const row = frappe.get_doc(cdt, cdn);
		const disc = flt(row.amount) - flt(row.confirmed_amount);
		frappe.model.set_value(cdt, cdn, "discrepancy", Math.round(disc * 100) / 100);
		_highlight_disputed_rows(frm);
	},
	amount(frm, cdt, cdn) {
		const row = frappe.get_doc(cdt, cdn);
		// Auto-fill confirmed_amount when amount is first set (new row)
		if (!flt(row.confirmed_amount)) {
			frappe.model.set_value(cdt, cdn, "confirmed_amount", flt(row.amount));
		}
	},
});

frappe.ui.form.on("Cash Handover", {
	refresh(frm) {
		_highlight_disputed_rows(frm);

		if (frm.doc.docstatus === 0) {
			frm.add_custom_button(__("Принять всё"), () => {
				(frm.doc.lines || []).forEach((row) => {
					frappe.model.set_value(
						row.doctype, row.name, "confirmed_amount", flt(row.amount)
					);
					frappe.model.set_value(row.doctype, row.name, "discrepancy", 0);
					frappe.model.set_value(row.doctype, row.name, "dispute_note", "");
				});
				frm.refresh_field("lines");
				_highlight_disputed_rows(frm);
			}, __("Действия"));
		}
	},
});

function _highlight_disputed_rows(frm) {
	(frm.doc.lines || []).forEach((row) => {
		const disc = flt(row.amount) - flt(row.confirmed_amount);
		const $row = frm.fields_dict.lines.grid.grid_rows_by_docname[row.name];
		if (!$row) return;
		if (Math.abs(disc) > 0.001) {
			$row.row.addClass("bnpl-row-disputed");
		} else {
			$row.row.removeClass("bnpl-row-disputed");
		}
	});
}
