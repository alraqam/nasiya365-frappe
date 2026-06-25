frappe.query_reports["Collections and Overdue"] = {
    filters: [
        {
            fieldname: "from_date",
            label: __("Сборы с даты"),
            fieldtype: "Date",
            default: frappe.datetime.add_months(frappe.datetime.get_today(), -1),
            reqd: 1,
        },
        {
            fieldname: "to_date",
            label: __("Сборы по дату"),
            fieldtype: "Date",
            default: frappe.datetime.get_today(),
            reqd: 1,
        },
        {
            fieldname: "branch",
            label: __("Филиал"),
            fieldtype: "Link",
            options: "Branch",
        },
        {
            fieldname: "only_overdue",
            label: __("Только просроченные"),
            fieldtype: "Check",
        },
    ],
    formatter(value, row, column, data, default_formatter) {
        value = default_formatter(value, row, column, data);
        if (column.fieldname === "days_overdue" && data && data.days_overdue > 0) {
            value = `<span style="color:var(--red-500);font-weight:600">${value}</span>`;
        }
        return value;
    },
};
