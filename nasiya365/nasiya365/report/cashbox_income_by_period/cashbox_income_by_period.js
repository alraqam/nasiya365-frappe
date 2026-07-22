// Copyright (c) 2026, Nasiya365 and contributors
// For license information, please see license.txt

frappe.query_reports["Cashbox Income by Period"] = {
    filters: [
        {
            fieldname: "from_date",
            label: __("С даты"),
            fieldtype: "Date",
            default: frappe.datetime.month_start(),
            reqd: 1,
        },
        {
            fieldname: "to_date",
            label: __("По дату"),
            fieldtype: "Date",
            default: frappe.datetime.get_today(),
            reqd: 1,
        },
        {
            fieldname: "cashbox",
            label: __("Касса"),
            fieldtype: "Link",
            options: "Cashbox",
        },
        {
            fieldname: "branch",
            label: __("Филиал"),
            fieldtype: "Link",
            options: "Branch",
        },
    ],

    formatter(value, row, column, data, default_formatter) {
        if (column.fieldname === "is_backdated") {
            return data && data.is_backdated
                ? `<span class="indicator-pill orange">${__("задним числом")}</span>`
                : `<span class="text-muted">—</span>`;
        }

        value = default_formatter(value, row, column, data);

        // Подсветим дату факта, когда она не совпадает с днём кассы.
        if (column.fieldname === "fact_date" && data && data.is_backdated) {
            value = `<span style="color:var(--orange-600)">${value}</span>`;
        }
        return value;
    },
};
