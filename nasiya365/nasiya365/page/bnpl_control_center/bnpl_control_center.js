frappe.pages["bnpl-control-center"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("BNPL Control Center"),
		single_column: true,
	});

	const view = new nasiya365.BnplControlCenter(page);
	view.render();
};

frappe.provide("nasiya365");

nasiya365.BnplControlCenter = class BnplControlCenter {
	constructor(page) {
		this.page = page;
		this.container = $(`
			<div class="bnpl-dashboard">
				<div class="bnpl-top-actions"></div>
				<div class="bnpl-kpi-grid"></div>
				<div class="bnpl-priority-grid">
					<div class="bnpl-panel bnpl-overdue-panel"></div>
					<div class="bnpl-panel bnpl-due-panel"></div>
				</div>
				<div class="bnpl-panel bnpl-activity-panel"></div>
			</div>
		`).appendTo(this.page.main);
	}

	render() {
		this.renderTopActions();
		this.refreshAll();
	}

	renderTopActions() {
		const actions = this.container.find(".bnpl-top-actions").empty();
		const buttons = [
			{
				label: "+ Продать в рассрочку",
				cssClass: "primary",
				handler: () => frappe.set_route("Form", "Sales Order", "new-sales-order-1"),
			},
			{
				label: "+ Клиент",
				cssClass: "secondary",
				handler: () => frappe.new_doc("Customer Profile"),
			},
			{
				label: "Принять платеж",
				cssClass: "warning",
				handler: () => frappe.new_doc("Payment Transaction"),
			},
		];
		buttons.forEach((button) => {
			$(`<button class="btn ${button.cssClass}">${__(button.label)}</button>`)
				.appendTo(actions)
				.on("click", button.handler);
		});
	}

	refreshAll() {
		this.fetchKpis();
		this.fetchPriorityLists();
		this.fetchRecentActivity();
	}

	fetchKpis() {
		frappe.call({
			method: "nasiya365.api.bnpl_dashboard.get_bnpl_kpis",
			callback: (r) => {
				const data = r.message || {};
				const cards = [
					{
						label: "Общий остаток",
						value: format_currency(data.outstanding_amount || 0),
						cssClass: "good",
						route: ["List", "Installment Plan", { status: ["in", ["Активный", "Просрочен"]] }],
					},
					{
						label: "Платежи сегодня",
						value: format_currency(data.due_today_amount || 0),
						cssClass: "warn",
						route: ["List", "Installment Plan"],
					},
					{
						label: "Просрочено",
						value: format_currency(data.overdue_amount || 0),
						cssClass: "risk",
						route: ["app", "overdue-collector"],
					},
					{
						label: "Собрано сегодня",
						value: format_currency(data.cash_collected_today || 0),
						cssClass: "good",
						route: ["List", "Payment Transaction", { payment_date: frappe.datetime.get_today() }],
					},
				];
				const root = this.container.find(".bnpl-kpi-grid").empty();
				cards.forEach((card) => {
					const node = $(`
						<div class="bnpl-kpi-card ${card.cssClass}">
							<div class="bnpl-kpi-label">${__(card.label)}</div>
							<div class="bnpl-kpi-value">${card.value}</div>
						</div>
					`).appendTo(root);
					node.on("click", () => frappe.set_route(...card.route));
				});
			},
		});
	}

	fetchPriorityLists() {
		frappe.call({
			method: "nasiya365.api.bnpl_dashboard.get_overdue_list",
			args: { limit: 10 },
			callback: (r) => this.renderOverduePanel(r.message || []),
		});

		frappe.call({
			method: "nasiya365.api.bnpl_dashboard.get_due_today_list",
			args: { limit: 10 },
			callback: (r) => this.renderDueTodayPanel(r.message || []),
		});
	}

	renderOverduePanel(rows) {
		const root = this.container.find(".bnpl-overdue-panel").empty();
		root.append(`<div class="bnpl-panel-title">Просроченные платежи</div>`);

		if (!rows.length) {
			root.append(this.emptyState("Нет просрочек. Отличный результат!"));
			return;
		}

		rows.forEach((row) => {
			const item = $(`
				<div class="bnpl-list-row">
					<div>
						<div class="bnpl-row-title">${frappe.utils.escape_html(row.customer_name || row.customer)}</div>
						<div class="bnpl-row-sub">${format_currency(row.amount_due)} · ${row.days_overdue} дн.</div>
					</div>
					<div class="bnpl-row-actions">
						<button class="btn btn-default btn-sm btn-call">Позвонить</button>
						<button class="btn btn-primary btn-sm btn-pay">Принять платеж</button>
					</div>
				</div>
			`).appendTo(root);

			item.find(".btn-call").on("click", () => {
				if (!row.phone) {
					frappe.msgprint(__("У клиента не указан основной номер"));
					return;
				}
				window.open(`tel:${row.phone}`, "_self");
			});
			item.find(".btn-pay").on("click", () => this.openPaymentDialog(row));
		});
	}

	renderDueTodayPanel(rows) {
		const root = this.container.find(".bnpl-due-panel").empty();
		root.append(`<div class="bnpl-panel-title">Платежи на сегодня</div>`);
		if (!rows.length) {
			root.append(this.emptyState("Сегодня нет плановых платежей"));
			return;
		}

		rows.forEach((row) => {
			$(`
				<div class="bnpl-list-row clickable">
					<div>
						<div class="bnpl-row-title">${frappe.utils.escape_html(row.customer_name || row.customer)}</div>
						<div class="bnpl-row-sub">${format_currency(row.amount_due)}</div>
					</div>
					<div class="bnpl-status-badge warn">Сегодня</div>
				</div>
			`)
				.appendTo(root)
				.on("click", () => frappe.set_route("Form", "Installment Plan", row.installment_plan));
		});
	}

	fetchRecentActivity() {
		frappe.call({
			method: "nasiya365.api.bnpl_dashboard.get_recent_activity",
			args: { limit: 6 },
			callback: (r) => this.renderActivityPanel(r.message || {}),
		});
	}

	renderActivityPanel(data) {
		const root = this.container.find(".bnpl-activity-panel").empty();
		root.append(`<div class="bnpl-panel-title">Последняя активность</div>`);

		const blocks = [
			{ title: "Новые продажи", rows: data.recent_sales || [], format: (r) => `${r.name} · ${format_currency(r.total_amount)}` },
			{ title: "Последние платежи", rows: data.recent_payments || [], format: (r) => `${r.name} · ${format_currency(r.amount)}` },
			{ title: "Новые клиенты", rows: data.new_clients || [], format: (r) => `${r.full_name || r.name}` },
		];

		const grid = $('<div class="bnpl-activity-grid"></div>').appendTo(root);
		blocks.forEach((block) => {
			const card = $('<div class="bnpl-activity-card"></div>').appendTo(grid);
			card.append(`<div class="bnpl-activity-title">${__(block.title)}</div>`);
			if (!block.rows.length) {
				card.append(this.emptyState("Пока нет данных"));
				return;
			}
			block.rows.forEach((row) => {
				card.append(`<div class="bnpl-activity-row">${frappe.utils.escape_html(block.format(row))}</div>`);
			});
		});
	}

	openPaymentDialog(row) {
		const dialog = new frappe.ui.Dialog({
			title: __("Принять платеж"),
			fields: [
				{ fieldname: "amount", fieldtype: "Currency", label: __("Сумма"), reqd: 1, default: row.amount_due },
				{ fieldname: "mode", fieldtype: "Select", label: __("Метод оплаты"), options: "Наличные\nКарта\nClick\nPayme\nПеревод", default: "Наличные" },
			],
			primary_action_label: __("Сохранить"),
			primary_action: (values) => {
				frappe.call({
					method: "nasiya365.api.bnpl_dashboard.accept_overdue_payment",
					args: { customer_or_plan: row.installment_plan, amount: values.amount, mode: values.mode },
					callback: () => {
						dialog.hide();
						frappe.show_alert({ message: __("Платеж принят"), indicator: "green" });
						this.refreshAll();
					},
				});
			},
		});
		dialog.show();
	}

	emptyState(text) {
		return `<div class="bnpl-empty">${__(text)}</div>`;
	}
};
