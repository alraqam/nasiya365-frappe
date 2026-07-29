frappe.pages["profit-and-loss"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Прибыль и поступления"),
		single_column: true,
	});

	const view = new nasiya365.ProfitAndLoss(page);
	view.render();
};

frappe.provide("nasiya365");

/** Tooltip texts (spec §11) — kept in one place so render + card markup agree. */
const PNL_TOOLTIPS = {
	method: __(
		"Прибыль по сделке признаётся после того, как полученные платежи покроют себестоимость проданного товара."
	),
	futureProfit: __(
		"Товарная маржа и все проценты по сделкам, оформленным в выбранном периоде. Сумма будет получена только при полной оплате."
	),
	costRecovery: __(
		"Часть полученных денег, которая возвращает сумму, ранее вложенную в приобретение товара. Это не убыток."
	),
	productMargin: __(
		"Часть прибыли от разницы между продажной ценой товара и его себестоимостью."
	),
	interestIncome: __(
		"Процентный доход по рассрочкам, уже признанный на основании полученных платежей."
	),
	earnedProfit: __(
		"Прибыль, признанная по действующим настройкам отчёта после учёта включённых расходов."
	),
};

/**
 * P&L report page («Прибыль и поступления») — Frappe Desk / jQuery.
 *
 * Presentation-only: all math/derivations live in the pure adapter
 * (public/js/pnl_adapter.js, loaded raw via frappe.require, exposing
 * window.Nasiya365PnL.{buildViewModel,formatMoney}). This class only
 * builds DOM, wires filters/buttons, calls the unchanged backend
 * (nasiya365.api.profit.get_profit_summary) and renders the adapter's
 * view-model. No financial logic is reimplemented here.
 */
nasiya365.ProfitAndLoss = class ProfitAndLoss {
	constructor(page) {
		this.page = page;
		this._vm = null;
		this._lastFilters = null;

		this._injectCss();

		this.container = $(`
			<div class="pnl-page">
				<div class="pnl-header">
					<div class="pnl-subtitle">
						<div>${__("Продажи учитываются по дате оформления сделки.")}</div>
						<div>${__("Поступления учитываются по дате фактического получения денег.")}</div>
					</div>
					<div class="pnl-method-line" title="${frappe.utils.escape_html(PNL_TOOLTIPS.method)}">
						${__("Метод признания прибыли: возмещение себестоимости")}
					</div>
				</div>
				<div class="pnl-filters"></div>
				<div class="pnl-state-region"></div>
				<div class="pnl-cards"></div>
				<div class="pnl-section pnl-section-sales">
					<div class="pnl-section-head">
						<div class="pnl-section-title">${__("1. Продажи, оформленные за период")}</div>
						<div class="pnl-section-desc">${__(
							"Показывает продажи, оформленные в выбранном периоде, и прибыль, которая может быть получена по этим сделкам."
						)}</div>
					</div>
					<div class="pnl-table-wrap"></div>
				</div>
				<div class="pnl-section pnl-section-recognized">
					<div class="pnl-section-head">
						<div class="pnl-section-title">${__("2. Поступления и заработанная прибыль")}</div>
						<div class="pnl-section-desc">${__(
							"Показывает деньги, фактически полученные в выбранном периоде, включая платежи по ранее оформленным сделкам."
						)}</div>
					</div>
					<div class="pnl-table-wrap"></div>
				</div>
				<div class="pnl-info-block"></div>
			</div>
		`).appendTo(this.page.main);
	}

	render() {
		this._renderFilters();
		// NB: no ?t= cache-bust here — frappe.require's asset-type detection breaks
		// on a query string. The adapter is stable; the CSS (which we iterate on)
		// is cache-busted via its <link> href instead.
		frappe.require("/assets/nasiya365/js/pnl_adapter.js", () => {
			const adapter = window.Nasiya365PnL;
			if (!adapter || typeof adapter.buildViewModel !== "function" || typeof adapter.formatMoney !== "function") {
				console.error(
					"[profit-and-loss] pnl_adapter.js failed to load or is missing expected exports:",
					adapter
				);
				this._showError();
				return;
			}
			this.load();
		});
	}

	/** Inject the raw pnl.css stylesheet into <head> once (guarded by id). */
	_injectCss() {
		if (document.getElementById("pnl-css")) return;
		const link = document.createElement("link");
		link.id = "pnl-css";
		link.rel = "stylesheet";
		// Cache-bust the raw (non-content-hashed) stylesheet so edits are always
		// picked up on dev without a manual hard-reload. (At prod deploy this file
		// is meant to be folded into the content-hashed nasiya365.bundle.css.)
		link.href = "/assets/nasiya365/css/pnl.css?t=" + Date.now();
		document.head.appendChild(link);
	}

	/* ——— Filters (§4) ——— */
	_renderFilters() {
		const wrap = this.container.find(".pnl-filters").empty();

		const fromWrap = $(`<div class="pnl-filter-field"></div>`).appendTo(wrap);
		this.fromDateCtrl = frappe.ui.form.make_control({
			parent: fromWrap,
			df: { fieldtype: "Date", fieldname: "from_date", label: __("Дата с"), reqd: 1 },
			render_input: true,
		});
		this.fromDateCtrl.set_input(frappe.datetime.add_months(frappe.datetime.get_today(), -1));

		const toWrap = $(`<div class="pnl-filter-field"></div>`).appendTo(wrap);
		this.toDateCtrl = frappe.ui.form.make_control({
			parent: toWrap,
			df: { fieldtype: "Date", fieldname: "to_date", label: __("Дата по"), reqd: 1 },
			render_input: true,
		});
		this.toDateCtrl.set_input(frappe.datetime.get_today());

		const branchWrap = $(`<div class="pnl-filter-field"></div>`).appendTo(wrap);
		this.branchCtrl = frappe.ui.form.make_control({
			parent: branchWrap,
			df: { fieldtype: "Link", fieldname: "branch", label: __("Филиал"), options: "Branch" },
			render_input: true,
		});

		const actions = $(`<div class="pnl-filter-actions"></div>`).appendTo(wrap);
		this.generateBtn = $(
			`<button type="button" class="btn btn-primary btn-sm pnl-btn-generate">${__(
				"Сформировать отчёт"
			)}</button>`
		).appendTo(actions);
		this.refreshBtn = $(
			`<button type="button" class="btn btn-default btn-sm pnl-btn-refresh">${__("Обновить")}</button>`
		).appendTo(actions);

		this.updatedEl = $(`<div class="pnl-updated"></div>`).appendTo(wrap);

		this.generateBtn.on("click", () => this.load());
		this.refreshBtn.on("click", () => this.load());
	}

	_getFilterValues() {
		const from_date = this.fromDateCtrl.get_value();
		const to_date = this.toDateCtrl.get_value();
		const branch = this.branchCtrl.get_value();
		return {
			from_date: from_date || undefined,
			to_date: to_date || undefined,
			branch: branch || undefined,
		};
	}

	_setButtonsDisabled(disabled) {
		[this.generateBtn, this.refreshBtn].forEach((btn) => btn && btn.prop("disabled", disabled));
	}

	/* ——— Data flow ——— */
	load() {
		const filters = this._getFilterValues();
		if (!filters.from_date || !filters.to_date) {
			frappe.show_alert({ message: __("Укажите обе даты периода"), indicator: "orange" });
			return;
		}

		this._lastFilters = filters;
		this._setButtonsDisabled(true);
		this._showLoading();

		frappe.call({
			method: "nasiya365.api.profit.get_profit_summary",
			args: filters,
			callback: (r) => {
				if (r.exc) {
					console.error("[profit-and-loss] get_profit_summary failed:", r.exc);
					this._showError();
					return;
				}
				try {
					const vm = window.Nasiya365PnL.buildViewModel(r.message || {});
					this._vm = vm;
					this._renderResult(vm);
					this._setUpdatedNow();
				} catch (e) {
					console.error("[profit-and-loss] failed to build/render view-model:", e);
					this._showError();
				}
			},
			error: (err) => {
				console.error("[profit-and-loss] get_profit_summary request failed:", err);
				this._showError();
			},
			always: () => {
				this._setButtonsDisabled(false);
			},
		});
	}

	/* ——— States (§12) ——— */
	_showLoading() {
		this.container.find(".pnl-state-region").empty();
		this.container.find(".pnl-cards").html(this._skeletonCardsHtml());
		this.container.find(".pnl-section-sales .pnl-table-wrap").html(this._skeletonTableHtml(3));
		this.container.find(".pnl-section-recognized .pnl-table-wrap").html(this._skeletonTableHtml(7));
		this.container.find(".pnl-info-block").empty();
	}

	_skeletonCardsHtml() {
		return Array.from({ length: 3 })
			.map(
				() => `
			<div class="pnl-card pnl-skeleton-card">
				<div class="pnl-skeleton-line pnl-skeleton-line--label"></div>
				<div class="pnl-skeleton-line pnl-skeleton-line--value"></div>
				<div class="pnl-skeleton-line pnl-skeleton-line--sub"></div>
			</div>
		`
			)
			.join("");
	}

	_skeletonTableHtml(rows) {
		const lines = Array.from({ length: rows })
			.map(() => `<div class="pnl-skeleton-line pnl-skeleton-line--row"></div>`)
			.join("");
		return `<div class="pnl-skeleton-table">${lines}</div>`;
	}

	_clearReport() {
		this.container.find(".pnl-cards").empty();
		this.container.find(".pnl-section-sales .pnl-table-wrap").empty();
		this.container.find(".pnl-section-recognized .pnl-table-wrap").empty();
		this.container.find(".pnl-info-block").empty();
	}

	_showError() {
		this._clearReport();
		this.container.find(".pnl-state-region").html(`
			<div class="pnl-state pnl-state--error">
				<div>${__("Не удалось сформировать отчёт.")}</div>
				<div>${__("Повторите попытку или обратитесь к администратору.")}</div>
			</div>
		`);
	}

	_showEmpty() {
		this._clearReport();
		this.container.find(".pnl-state-region").html(`
			<div class="pnl-state pnl-state--empty">
				<div>${__("За выбранный период продажи и платежи не найдены.")}</div>
				<div>${__("Измените даты или выберите другой филиал.")}</div>
			</div>
		`);
	}

	_renderResult(vm) {
		this.container.find(".pnl-state-region").empty();

		const isEmpty =
			vm.summary.collected === 0 && vm.summary.futureProfit === 0 && vm.sales.total.sales === 0;
		if (isEmpty) {
			this._showEmpty();
			return;
		}

		this._renderCards(vm);
		this._renderSalesTable(vm);
		this._renderRecognizedTable(vm);
		this._renderInfoBlock(vm);
	}

	/* ——— Cards (§5) ——— */
	_renderCards(vm) {
		const root = this.container.find(".pnl-cards").empty();
		const fmt = window.Nasiya365PnL.formatMoney;
		const esc = frappe.utils.escape_html;
		const earnedPositive = vm.summary.earnedProfit > 0;

		root.html(`
			<div class="pnl-card pnl-card--collected">
				<div class="pnl-card-label">${__("Поступило от клиентов")}</div>
				<div class="pnl-card-value">${esc(fmt(vm.summary.collected))}</div>
				<div class="pnl-card-sub">${__("Все фактически полученные платежи за выбранный период")}</div>
			</div>
			<div class="pnl-card pnl-card--earned${earnedPositive ? " pnl-card--positive" : ""}">
				<div class="pnl-card-label">${__("Заработанная прибыль")}</div>
				<div class="pnl-card-value">${esc(fmt(vm.summary.earnedProfit))}</div>
				<div class="pnl-card-sub">${__("Прибыль, признанная по фактически полученным платежам")}</div>
			</div>
			<div class="pnl-card pnl-card--future" title="${esc(PNL_TOOLTIPS.futureProfit)}">
				<div class="pnl-card-label">${__("Будущая прибыль по новым сделкам")}</div>
				<div class="pnl-card-value">${esc(fmt(vm.summary.futureProfit))}</div>
				<div class="pnl-card-sub">${__("При условии полной оплаты всех новых рассрочек")}</div>
			</div>
		`);
	}

	/* ——— Table 1 «Продажи, оформленные за период» (§6) ——— */
	_salesRows(vm) {
		return [
			{ label: __("Наличные"), bold: false, data: vm.sales.cash },
			{ label: __("Рассрочка"), bold: false, data: vm.sales.installment },
			{ label: __("Итого"), bold: true, data: vm.sales.total },
		];
	}

	_renderSalesTable(vm) {
		const wrap = this.container.find(".pnl-section-sales .pnl-table-wrap").empty();
		const fmt = window.Nasiya365PnL.formatMoney;
		const esc = frappe.utils.escape_html;

		const rowsHtml = this._salesRows(vm)
			.map((row) => {
				const d = row.data;
				const interestCell = d.interest === null ? "—" : esc(fmt(d.interest));
				return `
					<tr class="${row.bold ? "pnl-row-total" : ""}">
						<td class="pnl-col-label">${esc(row.label)}</td>
						<td class="pnl-col-num">${esc(fmt(d.sales))}</td>
						<td class="pnl-col-num">${esc(fmt(d.cost))}</td>
						<td class="pnl-col-num">${esc(fmt(d.margin))}</td>
						<td class="pnl-col-num">${interestCell}</td>
						<td class="pnl-col-num">${esc(fmt(d.totalProfit))}</td>
					</tr>
				`;
			})
			.join("");

		wrap.html(`
			<table class="pnl-table">
				<thead>
					<tr>
						<th class="pnl-col-label">${__("Тип продажи")}</th>
						<th class="pnl-col-num">${__("Продажи")}</th>
						<th class="pnl-col-num">${__("Себестоимость")}</th>
						<th class="pnl-col-num">${__("Маржа товара")}</th>
						<th class="pnl-col-num">${__("Проценты по рассрочке")}</th>
						<th class="pnl-col-num">${__("Общая прибыль")}</th>
					</tr>
				</thead>
				<tbody>${rowsHtml}</tbody>
			</table>
		`);
	}

	/* ——— Table 2 «Поступления и заработанная прибыль» (§7) ——— */
	_recognizedRows(vm) {
		const r = vm.recognized;
		return [
			{
				label: __("Поступило от клиентов"),
				value: r.collected,
				explain: __("Все полученные платежи"),
				bold: false,
				tooltip: null,
			},
			{
				label: __("Покрытие себестоимости"),
				value: r.costRecovery,
				explain: __("Часть поступлений, возвращающая стоимость товара"),
				bold: false,
				tooltip: PNL_TOOLTIPS.costRecovery,
			},
			{
				label: __("Заработанная товарная маржа"),
				value: r.productMargin,
				explain: __("Доход от разницы между ценой и себестоимостью"),
				bold: false,
				tooltip: PNL_TOOLTIPS.productMargin,
			},
			{
				label: __("Заработанные проценты"),
				value: r.interestIncome,
				explain: __("Признанный процентный доход по рассрочкам"),
				bold: false,
				tooltip: PNL_TOOLTIPS.interestIncome,
			},
			{
				label: __("Валовая прибыль"),
				value: r.grossProfit,
				explain: __("Товарная маржа и признанные проценты"),
				bold: true,
				tooltip: null,
			},
			{
				label: __("Операционные расходы"),
				value: r.operatingExpenses,
				explain: __("Расходы бизнеса за выбранный период"),
				bold: false,
				tooltip: null,
			},
			{
				label: __("Заработанная прибыль за период"),
				value: r.netProfit,
				explain: __("Итоговая прибыль по действующим настройкам"),
				bold: true,
				tooltip: PNL_TOOLTIPS.earnedProfit,
			},
		];
	}

	_renderRecognizedTable(vm) {
		const wrap = this.container.find(".pnl-section-recognized .pnl-table-wrap").empty();
		const fmt = window.Nasiya365PnL.formatMoney;
		const esc = frappe.utils.escape_html;

		const rowsHtml = this._recognizedRows(vm)
			.map((row) => {
				const labelHtml = row.tooltip
					? `<span class="pnl-tooltip-label" title="${esc(row.tooltip)}">${esc(row.label)}</span>`
					: esc(row.label);
				return `
					<tr class="${row.bold ? "pnl-row-total" : ""}">
						<td class="pnl-col-label">${labelHtml}</td>
						<td class="pnl-col-num">${esc(fmt(row.value))}</td>
						<td class="pnl-col-explain">${esc(row.explain)}</td>
					</tr>
				`;
			})
			.join("");

		wrap.html(`
			<table class="pnl-table">
				<thead>
					<tr>
						<th class="pnl-col-label">${__("Показатель")}</th>
						<th class="pnl-col-num">${__("Сумма")}</th>
						<th class="pnl-col-explain">${__("Объяснение")}</th>
					</tr>
				</thead>
				<tbody>${rowsHtml}</tbody>
			</table>
		`);
	}

	/* ——— Info block (§8) ——— */
	_renderInfoBlock(vm) {
		const root = this.container.find(".pnl-info-block").empty();
		const esc = frappe.utils.escape_html;
		const b = vm.basis;

		if (b.interestInProfit && b.expensesInProfit) return;

		const lines = [`${esc(__("База расчёта прибыли"))}: ${esc(b.profitBasis || "—")}`];
		if (!b.interestInProfit) lines.push(esc(__("Процентный доход: не входит в итог")));
		if (!b.expensesInProfit) lines.push(esc(__("Операционные расходы: не входят в итог")));

		root.html(`<div class="pnl-basis-info">${lines.map((l) => `<div>${l}</div>`).join("")}</div>`);
	}

	/* ——— «Обновлено» timestamp (§13) ——— */
	_setUpdatedNow() {
		if (!this.updatedEl) return;
		const now = new Date();
		const pad2 = (n) => String(n).padStart(2, "0");
		const stamp = `${pad2(now.getDate())}.${pad2(now.getMonth() + 1)}.${now.getFullYear()}, ${pad2(
			now.getHours()
		)}:${pad2(now.getMinutes())}`;
		this.updatedEl.text(`${__("Обновлено")}: ${stamp}`);
	}
};
