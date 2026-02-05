// Copyright (c) 2024, Nasiya365 and contributors
// For license information, please see license.txt

frappe.ui.form.on('Data Import Tool', {
    run_import: function (frm) {
        if (!frm.doc.csv_file) {
            frappe.msgprint("Please attach a CSV file.");
            return;
        }

        let start_import = function () {
            frappe.call({
                method: "nasiya365.nasiya365.doctype.data_import_tool.data_import_tool.run_bnpl_import",
                args: {
                    doc_name: frm.doc.name
                },
                freeze: true,
                freeze_message: "Importing Data...",
                callback: function (r) {
                    if (r.message) {
                        frappe.msgprint(r.message);
                    }
                }
            });
        };

        if (frm.is_dirty()) {
            frm.save().then(start_import);
        } else {
            start_import();
        }
    }
});
