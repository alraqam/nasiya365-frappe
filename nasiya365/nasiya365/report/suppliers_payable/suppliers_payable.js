frappe.query_reports["Suppliers Payable"] = {
    filters: [
        {
            fieldname: "branch",
            label: __("Филиал"),
            fieldtype: "Link",
            options: "Branch",
        },
        {
            fieldname: "only_outstanding",
            label: __("Только с долгом"),
            fieldtype: "Check",
            default: 1,
        },
    ],
    formatter(value, row, column, data, default_formatter) {
        value = default_formatter(value, row, column, data);
        if (column.fieldname === "oldest_unpaid_days" && data && data.oldest_unpaid_days > 30) {
            value = `<span style="color:var(--red-500);font-weight:600">${value}</span>`;
        }
        return value;
    },
};
