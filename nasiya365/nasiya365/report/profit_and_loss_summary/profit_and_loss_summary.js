frappe.query_reports["Profit and Loss Summary"] = {
    filters: [
        {
            fieldname: "from_date",
            label: __("С даты"),
            fieldtype: "Date",
            default: frappe.datetime.add_months(frappe.datetime.get_today(), -1),
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
            fieldname: "branch",
            label: __("Филиал"),
            fieldtype: "Link",
            options: "Branch",
        },
    ],
    formatter(value, row, column, data, default_formatter) {
        value = default_formatter(value, row, column, data);
        if (data && data.bold) {
            value = `<b>${value}</b>`;
        }
        return value;
    },
};
