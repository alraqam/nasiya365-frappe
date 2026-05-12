frappe.pages["bnpl-control-center"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Nasiya365 — Финансовый центр"),
		single_column: true,
	});

	const view = new nasiya365.BnplControlCenter(page);
	view.render();
};

frappe.provide("nasiya365");

/**
 * BNPL decision dashboard (Frappe Desk / jQuery).
 * Loads data via legacy whitelisted methods so the page works even when workers serve an older
 * `bnpl_dashboard.py` (calling a missing snapshot method would still trigger Frappe’s error toast).
 * `get_control_center_snapshot` remains on the server for API use after `bench restart`.
 */
nasiya365.BnplControlCenter = class BnplControlCenter {
	constructor(page) {
		this.page = page;
		this._snapshot = null;
		this.container = $(`
			<div class="bnpl-dashboard">
				<div class="bnpl-cta-row"></div>
				<div class="bnpl-needs-attention"></div>
				<div class="bnpl-kpi-grid"></div>
				<div class="bnpl-cashflow"></div>
				<div class="bnpl-split-grid">
					<div class="bnpl-panel bnpl-due-panel"></div>
					<div class="bnpl-panel bnpl-future-panel"></div>
				</div>
				<div class="bnpl-panel bnpl-activity-panel"></div>
			</div>
		`).appendTo(this.page.main);
	}

	render() {
		this.renderCtaRow();
		this.refreshAll();
	}

	renderCtaRow() {
		const row = this.container.find(".bnpl-cta-row").empty();
		const primary = {
			label: __("Наличные продажи"),
			cssClass: "bnpl-cta-primary",
			handler: () => frappe.set_route("List", "Sales Order"),
		};
		const secondaries = [
			{
				label: __("Принять платеж"),
				cssClass: "bnpl-cta-secondary",
				handler: () => frappe.new_doc("Payment Transaction"),
			},
			{
				label: __("Добавить клиента"),
				cssClass: "bnpl-cta-secondary",
				handler: () => frappe.new_doc("Customer Profile"),
			},
		];
		$(`<button type="button" class="btn btn-primary bnpl-cta-main">${frappe.utils.escape_html(primary.label)}</button>`)
			.appendTo(row)
			.on("click", primary.handler);
		const secWrap = $('<div class="bnpl-cta-secondaries"></div>').appendTo(row);
		secondaries.forEach((b) => {
			$(`<button type="button" class="btn btn-default ${b.cssClass}">${frappe.utils.escape_html(b.label)}</button>`)
				.appendTo(secWrap)
				.on("click", b.handler);
		});
	}

	refreshAll() {
		this.container.find(".bnpl-banner").remove();
		this.refreshAllLegacy();
	}

	applySnapshot(msg) {
		this._snapshot = msg || {};
		this.renderKpiGrid(this._snapshot.kpi_cards || []);
		this.renderNeedsAttention(this._snapshot.needs_attention || {});
		this.renderCashflow(this._snapshot.cashflow || {});
		this.renderTodaysActions(this._snapshot.due_today_actions || []);
		this.renderFutureReady(this._snapshot.future_modules || this.defaultFutureModules());
		this.renderActivityTimeline((this._snapshot.activity || {}).events || []);
	}

	defaultFutureModules() {
		return {
			risk_scoring: { status: "planned", hint: __("Расширение профиля риска клиента") },
			fraud_signals: { status: "planned", hint: __("Сигналы мошенничества и аномалий") },
			forecasting: { status: "planned", hint: __("Прогноз поступлений и дефолта") },
			collection_kpis: { status: "planned", hint: __("Эффективность коллекшн-команды") },
		};
	}

	/** Chain legacy API calls when aggregated snapshot is unavailable on the server. */
	refreshAllLegacy() {
		frappe.call({
			method: "nasiya365.api.bnpl_dashboard.get_bnpl_kpis",
			callback: (kpi_r) => {
				if (kpi_r.exc) {
					this.showFatalLoadError();
					return;
				}
				const kpis = kpi_r.message || {};
				frappe.call({
					method: "nasiya365.api.bnpl_dashboard.get_overdue_list",
					args: { limit: 500 },
					callback: (ov_r) => {
						if (ov_r.exc) {
							this.showFatalLoadError();
							return;
						}
						const overdue = ov_r.message || [];
						frappe.call({
							method: "nasiya365.api.bnpl_dashboard.get_due_today_list",
							args: { limit: 15 },
							callback: (due_r) => {
								if (due_r.exc) {
									this.showFatalLoadError();
									return;
								}
								const due = due_r.message || [];
								frappe.call({
									method: "nasiya365.api.bnpl_dashboard.get_recent_activity",
									args: { limit: 8 },
									callback: (act_r) => {
										if (act_r.exc) {
											this.showFatalLoadError();
											return;
										}
										const snap = this.buildLegacySnapshot(
											kpis,
											overdue,
											due,
											act_r.message || {}
										);
										this.applySnapshot(snap);
									},
								});
							},
						});
					},
				});
			},
		});
	}

	showFatalLoadError() {
		this._snapshot = null;
		this.container.prepend(
			`<div class="bnpl-banner bnpl-banner--risk">${frappe.utils.escape_html(
				__("Не удалось загрузить данные. Проверьте права доступа и обновите страницу.")
			)}</div>`
		);
		this.renderKpiGrid([]);
		this.renderNeedsAttention({});
		this.renderCashflow({});
		this.renderTodaysActions([]);
		this.renderFutureReady(this.defaultFutureModules());
		this.renderActivityTimeline([]);
	}

	buildLegacySnapshot(kpis, overdueRows, dueRows, recent) {
		const toF = (v) =>
			typeof flt === "function" ? flt(v) : parseFloat(v == null || v === "" ? 0 : v) || 0;
		const toI = (v) =>
			typeof cint === "function" ? cint(v) : parseInt(String(v == null ? 0 : v), 10) || 0;
		const k = kpis || {};
		const overdue = overdueRows || [];
		const outstanding = toF(k.outstanding_amount);
		const overdueAmt = toF(k.overdue_amount);
		const dueToday = toF(k.due_today_amount);
		const collected = toF(k.cash_collected_today);
		const overduePct = outstanding ? Math.round((overdueAmt / outstanding) * 10000) / 100 : 0;

		const highRiskCustomers = new Set();
		overdue.forEach((row) => {
			if (toI(row.days_overdue) > 7) {
				highRiskCustomers.add(row.customer);
			}
		});

		let progressPct = 0;
		if (dueToday > 0) {
			progressPct = Math.min(100, Math.round((collected / dueToday) * 1000) / 10);
		} else {
			progressPct = collected > 0 ? 100 : 0;
		}

		const activeContracts =
			k.active_contracts != null && k.active_contracts !== undefined
				? toI(k.active_contracts)
				: 0;
		const revenueMtd =
			k.revenue_mtd != null && k.revenue_mtd !== undefined ? toF(k.revenue_mtd) : 0;

		const todayStr = frappe.datetime.get_today();
		const kpi_cards = [
			{
				id: "outstanding",
				label: __("Всего к получению"),
				value: outstanding,
				subtext: __("Активные рассрочки"),
				tone: "neutral",
				trend: null,
				route: ["List", "Installment Plan", { status: ["in", ["Активный", "Просрочен"]] }],
			},
			{
				id: "overdue",
				label: __("Просрочено"),
				value: overdueAmt,
				subtext: `${overduePct}% ` + __("от портфеля"),
				tone: "risk",
				trend: null,
				route: ["app", "overdue-collector"],
			},
			{
				id: "due_today",
				label: __("Ожидается сегодня"),
				value: dueToday,
				subtext: __("Плановые платежи"),
				tone: "pending",
				trend: null,
				route: ["List", "Installment Plan"],
			},
			{
				id: "collected_today",
				label: __("Собрано сегодня"),
				value: collected,
				subtext: __("Фактически получено"),
				tone: "success",
				trend: null,
				route: ["List", "Payment Transaction", { payment_date: todayStr }],
			},
			{
				id: "active_contracts",
				label: __("Активные договоры"),
				value: activeContracts,
				subtext: __("Количество планов"),
				tone: "neutral",
				trend: null,
				route: ["List", "Installment Plan", { status: ["in", ["Активный", "Просрочен"]] }],
			},
			{
				id: "revenue_mtd",
				label: __("Выручка (МТД)"),
				value: revenueMtd,
				subtext: __("Завершенные платежи"),
				tone: "success",
				trend: null,
				route: ["List", "Payment Transaction"],
			},
		];

		const topOverdue = overdue.slice(0, 5);

		return {
			kpi_cards,
			needs_attention: {
				overdue_payments_count: overdue.length,
				high_risk_clients: highRiskCustomers.size,
				amount_at_risk: overdueAmt,
				top_overdue: topOverdue,
			},
			cashflow: {
				expected_today: dueToday,
				collected_today: collected,
				progress_pct: progressPct,
			},
			due_today_actions: dueRows || [],
			activity: { events: this.buildTimelineFromRecent(recent) },
			future_modules: this.defaultFutureModules(),
		};
	}

	buildTimelineFromRecent(data) {
		const events = [];
		const d = data || {};
		(d.recent_payments || []).forEach((row) => {
			events.push({
				kind: "payment_received",
				tone: "success",
				timestamp: String(row.creation || row.payment_date || ""),
				title: __("Платеж получен"),
				detail: `${row.name} · ${format_currency(row.amount)}`,
				reference: row.name,
			});
		});
		(d.recent_sales || []).forEach((row) => {
			events.push({
				kind: "new_sale",
				tone: "neutral",
				timestamp: String(row.creation || ""),
				title: __("Новая продажа"),
				detail: `${row.name} · ${format_currency(row.total_amount)}`,
				reference: row.name,
			});
		});
		(d.new_clients || []).forEach((row) => {
			events.push({
				kind: "new_client",
				tone: "info",
				timestamp: String(row.creation || ""),
				title: __("Новый клиент"),
				detail: row.full_name || row.name,
				reference: row.name,
			});
		});
		events.sort((a, b) => String(b.timestamp).localeCompare(String(a.timestamp)));
		return events.slice(0, 24);
	}

	/* ——— KPI cards (reusable pattern) ——— */
	formatKpiValue(card) {
		if (card.id === "active_contracts") {
			return String(Math.round(Number(card.value || 0)));
		}
		return format_currency(card.value || 0);
	}

	renderTrend(trend) {
		if (!trend || trend.direction === "flat") {
			return `<span class="bnpl-trend bnpl-trend--flat">—</span>`;
		}
		const arrow = trend.direction === "up" ? "↑" : "↓";
		const cls =
			trend.direction === "up" ? "bnpl-trend bnpl-trend--up" : "bnpl-trend bnpl-trend--down";
		return `<span class="${cls}">${arrow} ${frappe.utils.escape_html(String(trend.pct))}%</span>`;
	}

	renderKpiGrid(cards) {
		const root = this.container.find(".bnpl-kpi-grid").empty();
		if (!cards.length) {
			root.append(this.renderOnboardingEmpty());
			return;
		}
		cards.forEach((card) => {
			const tone = card.tone || "neutral";
			const sub = card.subtext ? `<div class="bnpl-kpi-sub">${frappe.utils.escape_html(card.subtext)}</div>` : "";
			const trend = this.renderTrend(card.trend);
			const node = $(`
				<div class="bnpl-kpi-card bnpl-kpi-card--${tone}" data-kpi-id="${frappe.utils.escape_html(card.id || "")}">
					<div class="bnpl-kpi-label">${frappe.utils.escape_html(__(card.label))}</div>
					<div class="bnpl-kpi-value-row">
						<span class="bnpl-kpi-value">${frappe.utils.escape_html(this.formatKpiValue(card))}</span>
						${trend}
					</div>
					${sub}
				</div>
			`).appendTo(root);
			if (card.route && card.route.length) {
				node.attr("role", "button").attr("tabindex", "0");
				node.on("click keypress", (e) => {
					if (e.type === "keypress" && e.which !== 13) return;
					frappe.set_route(...card.route);
				});
			}
		});
	}

	/* ——— Needs attention ——— */
	renderNeedsAttention(data) {
		const root = this.container.find(".bnpl-needs-attention").empty();
		const top = (data.top_overdue || []).slice(0, 5);
		const overdueCount = data.overdue_payments_count || 0;
		const highRisk = data.high_risk_clients || 0;
		const atRisk = data.amount_at_risk || 0;

		const header = $(`
			<div class="bnpl-attention-header">
				<div>
					<div class="bnpl-attention-title">${__("Требует внимания")}</div>
					<div class="bnpl-attention-sub">${__("Риск и коллекции — сфокусируйтесь на просрочке")}</div>
				</div>
				<button type="button" class="btn btn-default btn-sm bnpl-goto-collections">${__("Коллекции")}</button>
			</div>
		`).appendTo(root);
		header.find(".bnpl-goto-collections").on("click", () => frappe.set_route("overdue-collector"));

		const stats = $(`<div class="bnpl-attention-stats"></div>`).appendTo(root);
		[
			{ label: __("Просроченных платежей"), value: String(overdueCount), tone: "risk" },
			{ label: __("Клиентов > 7 дн."), value: String(highRisk), tone: "risk" },
			{ label: __("Сумма под риском"), value: format_currency(atRisk), tone: "risk" },
		].forEach((s) => {
			stats.append(`
				<div class="bnpl-attention-stat bnpl-attention-stat--${s.tone}">
					<div class="bnpl-attention-stat-label">${frappe.utils.escape_html(s.label)}</div>
					<div class="bnpl-attention-stat-value">${frappe.utils.escape_html(s.value)}</div>
				</div>
			`);
		});

		if (!top.length) {
			root.append(
				`<div class="bnpl-empty bnpl-empty--onboarding">${__("Просрочек нет — портфель в норме.")}</div>`
			);
			return;
		}

		root.append(`<div class="bnpl-attention-list-title">${__("Топ-5 просрочек")}</div>`);
		const list = $('<div class="bnpl-attention-list"></div>').appendTo(root);
		top.forEach((row) => {
			const item = $(`
				<div class="bnpl-attention-row">
					<div class="bnpl-attention-row-main">
						<div class="bnpl-row-title">${frappe.utils.escape_html(row.customer_name || row.customer)}</div>
						<div class="bnpl-row-sub">${format_currency(row.amount_due)} · ${row.days_overdue} ${__("дн.")}</div>
					</div>
					<div class="bnpl-row-actions">
						<button type="button" class="btn btn-default btn-sm btn-call">${__("Звонок")}</button>
						<button type="button" class="btn btn-primary btn-sm btn-mark-paid">${__("Оплачено")}</button>
					</div>
				</div>
			`).appendTo(list);
			item.find(".btn-call").on("click", () => {
				if (!row.phone) {
					frappe.msgprint(__("У клиента не указан основной номер"));
					return;
				}
				window.open(`tel:${row.phone}`, "_self");
			});
			item.find(".btn-mark-paid").on("click", () => this.openPaymentDialog(row));
		});
	}

	/* ——— Cashflow ——— */
	renderCashflow(cf) {
		const root = this.container.find(".bnpl-cashflow").empty();
		const expected = cf.expected_today || 0;
		const collected = cf.collected_today || 0;
		const pct = cf.progress_pct != null ? cf.progress_pct : 0;
		const width = Math.min(100, Math.max(0, pct));
		root.append(`
			<div class="bnpl-cashflow-inner">
				<div class="bnpl-cashflow-head">
					<div class="bnpl-cashflow-title">${__("Денежный поток сегодня")}</div>
					<div class="bnpl-cashflow-pct">${frappe.utils.escape_html(String(width))}% ${__("собрано")}</div>
				</div>
				<div class="bnpl-cashflow-bar">
					<div class="bnpl-cashflow-bar-fill" style="width:${width}%"></div>
				</div>
				<div class="bnpl-cashflow-compare">
					<div><span class="bnpl-cf-label">${__("Ожидается")}</span> <strong>${frappe.utils.escape_html(format_currency(expected))}</strong></div>
					<div><span class="bnpl-cf-label">${__("Собрано")}</span> <strong>${frappe.utils.escape_html(format_currency(collected))}</strong></div>
				</div>
			</div>
		`);
	}

	/* ——— Today's actions ——— */
	renderTodaysActions(rows) {
		const root = this.container.find(".bnpl-due-panel").empty();
		root.append(`<div class="bnpl-panel-title">${__("Действия на сегодня")}</div>`);
		root.append(
			`<div class="bnpl-panel-hint">${__("Сортировка: частичные платежи и крупные суммы выше")}</div>`
		);

		if (!rows.length) {
			root.append(this.renderOnboardingEmpty("due"));
			return;
		}

		rows.forEach((row) => {
			const urg =
				row.urgency === "high"
					? `<span class="bnpl-urgency bnpl-urgency--high">${__("Срочно")}</span>`
					: `<span class="bnpl-urgency bnpl-urgency--normal">${__("Сегодня")}</span>`;
			// Aggregated by contract — show installment count when >1.
			const installment_count = cint(row.due_count || 0);
			const count_label = installment_count > 1 ? ` <span class="text-muted">· ${installment_count} ${__("взн.")}</span>` : "";
			const item = $(`
				<div class="bnpl-list-row bnpl-due-row">
					<div>
						<div class="bnpl-row-title">${frappe.utils.escape_html(row.customer_name || row.customer)}</div>
						<div class="bnpl-row-sub">${format_currency(row.amount_due)}${count_label}</div>
					</div>
					<div class="bnpl-due-actions">
						${urg}
						<button type="button" class="btn btn-default btn-sm btn-remind">${__("Напомнить")}</button>
						<button type="button" class="btn btn-primary btn-sm btn-accept">${__("Принять платеж")}</button>
					</div>
				</div>
			`).appendTo(root);

			item.find(".btn-remind").on("click", (e) => {
				e.stopPropagation();
				frappe.call({
					method: "nasiya365.api.bnpl_dashboard.send_due_payment_reminder",
					args: {
						installment_plan: row.installment_plan,
						amount: row.amount_due,
					},
					callback: (r) => {
						if (!r.exc) {
							frappe.show_alert({ message: __("Напоминание отправлено"), indicator: "green" });
						}
					},
				});
			});
			item.find(".btn-accept").on("click", (e) => {
				e.stopPropagation();
				this.openPaymentDialog({
					installment_plan: row.installment_plan,
					amount_due: row.amount_due,
					customer_name: row.customer_name,
					customer: row.customer,
				});
			});
		});
	}

	/* ——— Future-ready placeholder strip ——— */
	renderFutureReady(modules) {
		const root = this.container.find(".bnpl-future-panel").empty();
		root.append(`<div class="bnpl-panel-title">${__("Дальше: аналитика")}</div>`);
		root.append(
			`<div class="bnpl-panel-hint">${__("Зарезервировано под скоринг, антифрод и прогнозы")}</div>`
		);
		const keys = [
			["risk_scoring", __("Скоринг риска")],
			["fraud_signals", __("Антифрод-сигналы")],
			["forecasting", __("Прогнозирование")],
			["collection_kpis", __("Метрики коллекций")],
		];
		keys.forEach(([k, title]) => {
			const m = modules[k];
			if (!m) return;
			const hint = __(m.hint || "");
			root.append(`
				<div class="bnpl-future-chip">
					<span class="bnpl-future-dot"></span>
					<div>
						<div class="bnpl-future-name">${frappe.utils.escape_html(title)}</div>
						<div class="bnpl-future-hint">${frappe.utils.escape_html(hint)}</div>
					</div>
				</div>
			`);
		});
	}

	/* ——— Unified activity timeline ——— */
	iconForKind(kind) {
		const map = {
			payment_received: "fa fa-check-circle",
			new_sale: "fa fa-shopping-cart",
			new_client: "fa fa-user-plus",
			missed_payment: "fa fa-exclamation-triangle",
		};
		return map[kind] || "fa fa-circle";
	}

	renderActivityTimeline(events) {
		const root = this.container.find(".bnpl-activity-panel").empty();
		root.append(`<div class="bnpl-panel-title">${__("Лента активности")}</div>`);

		if (!events.length) {
			root.append(this.emptyState(__("Пока нет событий")));
			return;
		}

		const list = $('<div class="bnpl-timeline"></div>').appendTo(root);
		events.forEach((ev) => {
			const tone = ev.tone || "neutral";
			const icon = this.iconForKind(ev.kind);
			const ts = ev.timestamp
				? frappe.datetime.prettyDate(ev.timestamp) || ev.timestamp
				: "";
			const row = $(`
				<div class="bnpl-timeline-row bnpl-timeline-row--${tone}">
					<div class="bnpl-timeline-icon"><i class="${icon}" aria-hidden="true"></i></div>
					<div class="bnpl-timeline-body">
						<div class="bnpl-timeline-title">${frappe.utils.escape_html(__(ev.title))}</div>
						<div class="bnpl-timeline-detail">${frappe.utils.escape_html(ev.detail || "")}</div>
						<div class="bnpl-timeline-ts">${frappe.utils.escape_html(ts)}</div>
					</div>
				</div>
			`).appendTo(list);
			row.on("click", () => {
				if (ev.kind === "new_sale" && ev.reference) {
					frappe.set_route("Form", "Sales Order", ev.reference);
				} else if (ev.kind === "payment_received" && ev.reference) {
					frappe.set_route("Form", "Payment Transaction", ev.reference);
				} else if (ev.kind === "new_client" && ev.reference) {
					frappe.set_route("Form", "Customer Profile", ev.reference);
				} else if (ev.kind === "missed_payment" && ev.reference) {
					frappe.set_route("Form", "Installment Plan", ev.reference);
				}
			});
		});
	}

	openPaymentDialog(row) {
		const goToFullForm = () => {
			frappe.route_options = {
				customer: row.customer,
				reference_doctype: "Installment Plan",
				reference_name: row.installment_plan,
			};
			frappe.new_doc("Payment Transaction");
		};

		const showDialog = (methodOptions, defaultMethod) => {
			const dialog = new frappe.ui.Dialog({
				title: __("Принять платеж"),
				fields: [
					{ fieldname: "amount", fieldtype: "Currency", label: __("Сумма"), reqd: 1, default: row.amount_due },
					{
						fieldname: "mode",
						fieldtype: "Select",
						label: __("Метод оплаты"),
						options: methodOptions,
						default: defaultMethod,
					},
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
				secondary_action_label: __("Расширенная форма"),
				secondary_action: () => {
					dialog.hide();
					goToFullForm();
				},
			});
			dialog.show();
		};

		// Read options from Merchant Settings (admin-editable, single source of truth).
		frappe.call({
			method: "nasiya365.api.bnpl_dashboard.get_payment_methods",
			callback(r) {
				const raw = r.message && r.message.length ? r.message : ["Наличные USD", "Наличные UZS", "Карта"];
				const methods = raw.filter(m => m !== "Наличные");
				const defaultMethod = methods.includes("Наличные USD") ? "Наличные USD" : methods[0];
				showDialog(methods.join("\n"), defaultMethod);
			},
		});
	}

	emptyState(text) {
		return `<div class="bnpl-empty">${frappe.utils.escape_html(__(text))}</div>`;
	}

	renderOnboardingEmpty(context) {
		const steps = [
			{ n: 1, label: __("Создайте клиента"), route: () => frappe.new_doc("Customer Profile") },
			{ n: 2, label: __("Добавьте товар"), route: () => frappe.new_doc("Product") },
			{
				n: 3,
				label: __("Наличные продажи"),
				route: () => frappe.set_route("List", "Sales Order"),
			},
		];
		const wrap = $(`<div class="bnpl-onboarding"></div>`);
		wrap.append(`<div class="bnpl-onboarding-title">${__("Начните за 3 шага")}</div>`);
		const ul = $('<ol class="bnpl-onboarding-steps"></ol>');
		steps.forEach((s) => {
			const li = $(`<li>${frappe.utils.escape_html(s.label)}</li>`);
			ul.append(li);
		});
		wrap.append(ul);
		const actions = $('<div class="bnpl-onboarding-actions"></div>');
		steps.forEach((s) => {
			$(`<button type="button" class="btn btn-xs btn-default">${frappe.utils.escape_html(s.label)}</button>`)
				.appendTo(actions)
				.on("click", () => s.route());
		});
		wrap.append(actions);
		if (context === "due") {
			wrap.find(".bnpl-onboarding-title").text(__("Сегодня нет плановых платежей"));
		}
		return wrap;
	}
};
