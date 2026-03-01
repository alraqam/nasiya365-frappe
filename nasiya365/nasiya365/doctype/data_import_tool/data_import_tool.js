// Copyright (c) 2024, Nasiya365 and contributors
// For license information, please see license.txt

frappe.ui.form.on('Data Import Tool', {
    refresh: function (frm) {
        // Add "Delete Imported Data" button in the toolbar
        frm.add_custom_button(__('Удалить импортированные данные'), function () {
            let import_type = frm.doc.import_type;

            frappe.confirm(
                __('Вы уверены, что хотите удалить все данные типа <b>{0}</b>?', [import_type]),
                function () {
                    frappe.call({
                        method: "nasiya365.cleanup_import.cleanup_imported_data",
                        args: {
                            import_type: import_type
                        },
                        freeze: true,
                        freeze_message: __("Удаление данных..."),
                        callback: function (r) {
                            if (r.message) {
                                frappe.msgprint(r.message.message);
                                frm.set_value('import_log', r.message.message);
                                frm.save();
                            }
                        }
                    });
                }
            );
        }, __('Действия'));

        // Add "Delete ALL Imported Data" button
        frm.add_custom_button(__('Удалить ВСЕ импортированные данные'), function () {
            frappe.confirm(
                __('Вы уверены, что хотите удалить <b>ВСЕ</b> импортированные данные? Это действие нельзя отменить!'),
                function () {
                    frappe.call({
                        method: "nasiya365.cleanup_import.cleanup_imported_data",
                        args: {
                            import_type: "all"
                        },
                        freeze: true,
                        freeze_message: __("Удаление всех данных..."),
                        callback: function (r) {
                            if (r.message) {
                                frappe.msgprint(r.message.message);
                                frm.set_value('import_log', r.message.message);
                                frm.save();
                            }
                        }
                    });
                }
            );
        }, __('Действия'));
    },

    run_import: function (frm) {
        if (!frm.doc.csv_file) {
            frappe.msgprint(__("Прикрепите CSV файл."));
            return;
        }

        let start_import = function () {
            frappe.call({
                method: "nasiya365.nasiya365.doctype.data_import_tool.data_import_tool.run_bnpl_import",
                args: {
                    doc_name: frm.doc.name
                },
                freeze: true,
                freeze_message: __("Импорт данных..."),
                callback: function (r) {
                    if (r.message) {
                        frappe.msgprint(r.message);
                        frm.reload_doc();
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
