frappe.provide("nasiya365");

/** Safe currency for desk (avoids blank rows if global format_currency is missing). */
nasiya365._fmt_money = function (v) {
	const n = flt(v);
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
			const product = row.product_name ? `<span class="bnpl-row-product">${frappe.utils.escape_html(row.product_name)}</span>` : "";
			const overdue_label = row.days_overdue ? ` · <span style="color:var(--red-500)">${row.days_overdue} ${__("дн. просрочки")}</span>` : "";
			const total_debt = flt(row.total_debt_today);
			const debt_label = total_debt > 0
				? `<span class="bnpl-row-debt">${__("Долг на сегодня")}: <b>${nasiya365._fmt_money(total_debt)}</b></span>`
				: "";
			// Aggregated row: one entry per contract. Count helps the collector see
			// when multiple installments roll up into the same line.
			const installment_count = cint(row.overdue_count || row.due_count || 0);
			const count_label = installment_count > 1 ? ` · ${installment_count} ${__("взн.")}` : "";
			const amount_label = installment_count > 1 ? __("К оплате") : __("Взнос");

			const last_call_label = row.last_call_outcome
				? `<div class="bnpl-row-sub" style="margin-top:2px;color:var(--text-muted);font-size:11px;">
					${__("Звонок")}: <b>${frappe.utils.escape_html(row.last_call_outcome)}</b>
					${row.last_call_date ? "· " + frappe.format(row.last_call_date, {fieldtype: "Datetime"}) : ""}
					${row.last_promised_date ? " · " + __("Обещал") + ": " + frappe.format(row.last_promised_date, {fieldtype: "Date"}) : ""}
					${row.next_call_date ? " · <span style='color:var(--blue-500)'>" + __("Следующий звонок") + ": " + frappe.format(row.next_call_date, {fieldtype: "Date"}) + "</span>" : ""}
				</div>
				${row.last_call_notes ? `<div class="bnpl-row-sub" style="margin-top:2px;font-size:11px;color:var(--text-muted);white-space:pre-wrap;">${frappe.utils.escape_html(row.last_call_notes)}</div>` : ""}`
				: "";

			const node = $(`
				<div class="bnpl-list-row bnpl-list-row--clickable">
					<div class="bnpl-row-info" style="cursor:pointer;flex:1;min-width:0;">
						<div class="bnpl-row-title">${frappe.utils.escape_html(row.customer_name || row.customer)}${product}</div>
						<div class="bnpl-row-sub">
							${amount_label}: ${nasiya365._fmt_money(row.amount_due)}${count_label}${overdue_label}
						</div>
						${debt_label ? `<div class="bnpl-row-sub" style="margin-top:2px;">${debt_label}</div>` : ""}
						${last_call_label}
					</div>
					<div class="bnpl-row-actions">
						<button class="btn btn-default btn-sm btn-call">Позвонить</button>
						<button class="btn btn-default btn-sm btn-log">Записать</button>
						<button class="btn btn-primary btn-sm btn-pay">Принять платеж</button>
					</div>
				</div>
			`).appendTo(list);

			// Clicking the info area opens the detail dialog
			node.find(".bnpl-row-info").on("click", () => this.openDetail(row));

			node.find(".btn-call").on("click", (e) => {
				e.stopPropagation();
				if (!row.phone) {
					frappe.msgprint(__("У клиента не указан основной номер"));
					return;
				}
				window.open(`tel:${row.phone}`, "_self");
			});

			node.find(".btn-log").on("click", (e) => {
				e.stopPropagation();
				this.openLogDialog(row);
			});

			node.find(".btn-pay").on("click", (e) => {
				e.stopPropagation();
				frappe.route_options = {
					customer: row.customer,
					reference_doctype: "Installment Plan",
					reference_name: row.installment_plan,
				};
				frappe.new_doc("Payment Transaction");
			});
		});
	}

	openDetail(row) {
		const fmt = nasiya365._fmt_money;
		const phone_link = row.phone
			? `<a href="tel:${frappe.utils.escape_html(row.phone)}">${frappe.utils.escape_html(row.phone)}</a>`
			: `<span class="text-muted">${__("Не указан")}</span>`;
		const product_row = row.product_name
			? `<tr><td>${__("Товар")}</td><td>${frappe.utils.escape_html(row.product_name)}</td></tr>`
			: "";
		const overdue_row = row.days_overdue
			? `<tr><td>${__("Просрочка")}</td><td style="color:var(--red-500)">${row.days_overdue} ${__("дн.")}</td></tr>`
			: "";
		const installment_count = cint(row.overdue_count || row.due_count || 0);
		const count_row = installment_count > 1
			? `<tr><td>${__("Взносов в строке")}</td><td><b>${installment_count}</b></td></tr>`
			: "";
		const total_debt = flt(row.total_debt_today);

		const d = new frappe.ui.Dialog({
			title: frappe.utils.escape_html(row.customer_name || row.customer),
			fields: [{
				fieldtype: "HTML",
				fieldname: "detail_html",
				options: `
					<table class="table table-bordered" style="margin-bottom:0;font-size:13px;">
						<tbody>
							<tr><td style="width:40%;color:var(--text-muted)">${__("Клиент")}</td>
							    <td><b>${frappe.utils.escape_html(row.customer_name || row.customer)}</b></td></tr>
							${product_row}
							<tr><td>${__("Телефон")}</td><td>${phone_link}</td></tr>
							<tr><td>${__("План рассрочки")}</td>
							    <td><a href="/app/installment-plan/${encodeURIComponent(row.installment_plan)}" target="_blank">
							        ${frappe.utils.escape_html(row.installment_plan)}</a></td></tr>
							<tr><td>${__("Дата платежа")}</td>
							    <td>${frappe.format(row.due_date, {fieldtype: "Date"})}${installment_count > 1 ? ` <span class="text-muted">(${__("самая ранняя")})</span>` : ""}</td></tr>
							${overdue_row}
							${count_row}
							<tr><td>${installment_count > 1 ? __("К оплате") : __("Сумма взноса")}</td>
							    <td><b>${fmt(row.amount_due)}</b></td></tr>
							${total_debt > 0 ? `<tr><td>${__("Долг на сегодня")}</td>
							    <td><b style="color:var(--red-500)">${fmt(total_debt)}</b></td></tr>` : ""}
							${row.last_call_outcome ? `<tr><td>${__("Последний звонок")}</td>
							    <td><b>${frappe.utils.escape_html(row.last_call_outcome)}</b>
							    ${row.last_call_date ? "· " + frappe.format(row.last_call_date, {fieldtype: "Datetime"}) : ""}</td></tr>` : ""}
							${row.last_promised_date ? `<tr><td>${__("Обещал оплатить")}</td>
							    <td>${frappe.format(row.last_promised_date, {fieldtype: "Date"})}</td></tr>` : ""}
							${row.next_call_date ? `<tr><td>${__("Следующий звонок")}</td>
							    <td style="color:var(--blue-500)">${frappe.format(row.next_call_date, {fieldtype: "Date"})}</td></tr>` : ""}
							${row.last_call_notes ? `<tr><td>${__("Комментарий")}</td>
							    <td style="white-space:pre-wrap">${frappe.utils.escape_html(row.last_call_notes)}</td></tr>` : ""}
						</tbody>
					</table>`,
			}],
			primary_action_label: __("Принять платеж"),
			primary_action() {
				frappe.route_options = {
					customer: row.customer,
					reference_doctype: "Installment Plan",
					reference_name: row.installment_plan,
				};
				frappe.new_doc("Payment Transaction");
				d.hide();
			},
			secondary_action_label: __("Открыть план"),
			secondary_action() {
				frappe.set_route("Form", "Installment Plan", row.installment_plan);
				d.hide();
			},
		});

		if (row.phone) {
			d.add_custom_action(__("Позвонить"), () => {
				window.open(`tel:${row.phone}`, "_self");
			});
		}

		d.show();
	}

	openLogDialog(row) {
		const d = new frappe.ui.Dialog({
			title: __("Записать звонок — {0}", [frappe.utils.escape_html(row.customer_name || row.customer)]),
			fields: [
				{
					fieldtype: "Select",
					fieldname: "outcome",
					label: __("Результат"),
					options: "\nОтвечено\nНе отвечено\nОбещал оплатить\nОтказался платить\nДругое",
					reqd: 1,
				},
				{
					fieldtype: "Date",
					fieldname: "promised_date",
					label: __("Обещанная дата оплаты"),
					depends_on: "eval:doc.outcome == 'Обещал оплатить'",
				},
				{
					fieldtype: "Date",
					fieldname: "next_call_date",
					label: __("Следующий звонок"),
				},
				{
					fieldtype: "Small Text",
					fieldname: "notes",
					label: __("Комментарий"),
				},
			],
			primary_action_label: __("Сохранить"),
			primary_action: (vals) => {
				d.hide();
				frappe.call({
					method: "nasiya365.api.bnpl_dashboard.log_collection_call",
					args: {
						installment_plan: row.installment_plan,
						outcome: vals.outcome,
						notes: vals.notes || "",
						promised_date: vals.promised_date || null,
						next_call_date: vals.next_call_date || null,
					},
					callback: (r) => {
						if (r.exc) return;
						frappe.show_alert({ message: __("Звонок записан"), indicator: "green" }, 4);
						// Refresh list to show updated last-call info
						this.fetch();
					},
				});
			},
		});
		d.show();
	}
};
