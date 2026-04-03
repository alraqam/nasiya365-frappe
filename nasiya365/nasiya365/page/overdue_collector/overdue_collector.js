frappe.provide("nasiya365");

/** Safe currency for desk (avoids blank rows if global format_currency is missing). */
nasiya365._fmt_money = function (v) {
	const n = frappe.utils.flt(v);
	try {
		if (typeof format_currency === "function") {
			return format_currency(n);
		}
	} catch (e) {
		/* ignore */
	}
	return frappe.format(n, { fieldtype: "Currency" });
};

frappe.pages["overdue-collector"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Коллекции (сегодня)"),
		single_column: true,
	});

	const view = new nasiya365.OverdueCollector(page);
	view.render();
};

nasiya365.OverdueCollector = class OverdueCollector {
	constructor(page) {
		this.page = page;
		this.root = $(`
			<div class="bnpl-overdue-screen">
				<div class="bnpl-overdue-toolbar">
					<input class="form-control" placeholder="Поиск клиента..." />
					<button class="btn btn-default btn-refresh">Обновить</button>
				</div>
				<div class="bnpl-overdue-list"></div>
				<div class="bnpl-overdue-list-late" style="margin-top:18px"></div>
			</div>
		`).appendTo(this.page.main);
		this.query = "";
	}

	render() {
		this.root.find("input").on("input", frappe.utils.debounce((e) => {
			this.query = (e.target.value || "").trim().toLowerCase();
			this.fetch();
		}, 300));
		this.root.find(".btn-refresh").on("click", () => this.fetch());
		this.fetch();
	}

	fetch() {
		frappe.call({
			method: "nasiya365.api.bnpl_dashboard.get_due_today_list",
			args: { limit: 200 },
			callback: (r) => {
				if (r.exc) {
					frappe.msgprint({
						title: __("Коллекции"),
						message: __("Не удалось загрузить «Платежи на сегодня». Проверьте права и журнал ошибок."),
						indicator: "red",
					});
				}
				let rows = r.message || [];
				if (this.query) {
					rows = rows.filter((row) => (row.customer_name || row.customer || "").toLowerCase().includes(this.query));
				}
				this.renderRows(rows, ".bnpl-overdue-list", __("Платежи на сегодня"));
			},
		});
		frappe.call({
			method: "nasiya365.api.bnpl_dashboard.get_overdue_list",
			args: { limit: 200 },
			callback: (r) => {
				if (r.exc) {
					frappe.msgprint({
						title: __("Коллекции"),
						message: __("Не удалось загрузить «Просроченные». Проверьте права и журнал ошибок."),
						indicator: "red",
					});
				}
				let rows = r.message || [];
				if (this.query) {
					rows = rows.filter((row) => (row.customer_name || row.customer || "").toLowerCase().includes(this.query));
				}
				this.renderRows(rows, ".bnpl-overdue-list-late", __("Просроченные"));
			},
		});
	}

	renderRows(rows, selector, title) {
		const list = this.root.find(selector).empty();
		list.append(`<div style="font-weight:600;margin:8px 0;">${frappe.utils.escape_html(title)}</div>`);
		if (!rows.length) {
			list.append('<div class="bnpl-empty">Список пуст</div>');
			return;
		}

		rows.forEach((row) => {
			const node = $(`
				<div class="bnpl-list-row">
					<div>
						<div class="bnpl-row-title">${frappe.utils.escape_html(row.customer_name || row.customer)}</div>
						<div class="bnpl-row-sub">${frappe.utils.escape_html(nasiya365._fmt_money(row.amount_due))}${row.days_overdue ? ` · ${row.days_overdue} дней просрочки` : ""}</div>
					</div>
					<div class="bnpl-row-actions">
						<button class="btn btn-default btn-sm btn-call">Позвонить</button>
						<button class="btn btn-primary btn-sm btn-pay">Принять платеж</button>
					</div>
				</div>
			`).appendTo(list);

			node.find(".btn-call").on("click", () => {
				if (!row.phone) {
					frappe.msgprint(__("У клиента не указан основной номер"));
					return;
				}
				window.open(`tel:${row.phone}`, "_self");
			});

			node.find(".btn-pay").on("click", () => {
				frappe.route_options = {
					customer: row.customer,
					reference_doctype: "Installment Plan",
					reference_name: row.installment_plan,
				};
				frappe.new_doc("Payment Transaction");
			});
		});
	}
};
