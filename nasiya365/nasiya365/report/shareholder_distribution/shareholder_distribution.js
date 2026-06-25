frappe.query_reports["Shareholder Distribution"] = {
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
};
