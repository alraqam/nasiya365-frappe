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

/**
 * Tooltip texts (refinements-brief.md §E, items 20-22) — kept in one place so
 * render + card markup agree. Exact strings per the brief; `earnedProfit` is
 * shared by BOTH the «Заработанная прибыль» top card AND the «Заработанная
 * товарная маржа» Table-2 row (brief item 21 applies the same text to both).
 */
const PNL_TOOLTIPS = {
	method: __(
		"Прибыль по сделке признаётся после того, как полученные платежи покроют себестоимость проданного товара."
	),
	futureProfit: __(
		"Сумма товарной маржи и процентов по сделкам периода. Процентная часть будет получена только при полной оплате рассрочек."
	),
	costRecovery: __(
		"Часть полученных денег, которая возвращает средства, вложенные в товар. Это не расход и не убыток."
	),
	earnedProfit: __(
		"Часть поступлений, признанная прибылью по действующей методике расчёта."
	),
	interestIncome: __("Признанный процентный доход по рассрочкам."),
	totalRow: __("Итог по выбранной базе расчёта прибыли."),
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
					<div class="pnl-formula"></div>
					<div class="pnl-table-wrap"></div>
				</div>
				<div class="pnl-info-block"></div>
			</div>
		`).appendTo(this.page.main);
	}

	render() {
		this._renderFilters();
		// Load the raw adapter via a cache-busted <script>. We can't cache-bust
		// through frappe.require (its asset-type detection breaks on a query
		// string), but a plain <script src="...?t="> accepts the query and so
		// always picks up adapter edits on dev. The adapter IIFE sets
		// window.Nasiya365PnL on load.
		const adapterScript = document.createElement("script");
		adapterScript.src = "/assets/nasiya365/js/pnl_adapter.js?t=" + Date.now();
		adapterScript.onload = () => {
			const adapter = window.Nasiya365PnL;
			if (!adapter || typeof adapter.buildViewModel !== "function" || typeof adapter.formatMoney !== "function") {
				console.error(
					"[profit-and-loss] pnl_adapter.js loaded but is missing expected exports:",
					adapter
				);
				this._showError();
				return;
			}
			this.load();
		};
		adapterScript.onerror = () => {
			console.error("[profit-and-loss] pnl_adapter.js failed to load");
			this._showError();
		};
		document.head.appendChild(adapterScript);
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
		// Item 17: empty branch already means "all branches" in the backend —
		// make that explicit via a placeholder instead of a blank field.
		if (this.branchCtrl && this.branchCtrl.$input) {
			this.branchCtrl.$input.attr("placeholder", __("Все филиалы"));
		}

		const actions = $(`<div class="pnl-filter-actions"></div>`).appendTo(wrap);
		// Primary action: apply the currently selected filters (dates/branch) and
		// (re)compute the report for that period.
		this.generateBtn = $(
			`<button type="button" class="btn btn-primary btn-sm pnl-btn-generate">${__(
				"Сформировать отчёт"
			)}</button>`
		).appendTo(actions);
		// Secondary action: re-run the report for the SAME filters already applied
		// (items 15/16) — same load() call, distinct labelling/affordance only.
		const refreshIconHtml =
			typeof frappe.utils.icon === "function" ? frappe.utils.icon("refresh", "sm") : "↻";
		this.refreshBtn = $(
			`<button type="button" class="btn btn-default btn-sm pnl-btn-refresh" title="${frappe.utils.escape_html(
				__("Обновить данные отчёта")
			)}">${refreshIconHtml} <span>${__("Обновить")}</span></button>`
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

	/* ——— States (§12) ———
	 * Item 2/29: every render path repaints cards + BOTH tables together, so a
	 * loading/empty/error state never leaves stale numbers from a previous
	 * period on screen while a new request is in flight.
	 */
	_showLoading() {
		this.container.find(".pnl-state-region").empty();
		this.container.find(".pnl-cards").html(this._skeletonCardsHtml());
		this.container.find(".pnl-section-sales .pnl-table-wrap").html(this._skeletonTableHtml(3));
		this.container.find(".pnl-section-recognized .pnl-formula").empty();
		this.container.find(".pnl-section-recognized .pnl-table-wrap").html(this._skeletonTableHtml(7));
		this.container.find(".pnl-info-block").empty();
	}

	_skeletonCardsHtml() {
		return Array.from({ length: 4 })
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
		this.container.find(".pnl-section-recognized .pnl-formula").empty();
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
		this._renderFormula(vm);
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
			<div class="pnl-card pnl-card--earned${
				earnedPositive ? " pnl-card--positive" : ""
			}" title="${esc(PNL_TOOLTIPS.earnedProfit)}">
				<div class="pnl-card-label">${__(
					"Заработанная прибыль"
				)} <span class="pnl-info-icon" title="${esc(PNL_TOOLTIPS.earnedProfit)}">ⓘ</span></div>
				<div class="pnl-card-value">${esc(fmt(vm.summary.earnedProfit))}</div>
				<div class="pnl-card-sub">${__("Прибыль, признанная по фактически полученным платежам")}</div>
			</div>
			<div class="pnl-card pnl-card--future" title="${esc(PNL_TOOLTIPS.futureProfit)}">
				<div class="pnl-card-label">${__(
					"Потенциал по наличным продажам"
				)} <span class="pnl-info-icon" title="${esc(PNL_TOOLTIPS.futureProfit)}">ⓘ</span></div>
				<div class="pnl-card-value">${esc(fmt(vm.sales.cash.totalProfit))}</div>
				<div class="pnl-card-sub">${__(
					"Товарная маржа наличных продаж, оформленных за период."
				)}</div>
			</div>
			<div class="pnl-card pnl-card--future" title="${esc(PNL_TOOLTIPS.futureProfit)}">
				<div class="pnl-card-label">${__(
					"Потенциал по рассрочкам"
				)} <span class="pnl-info-icon" title="${esc(PNL_TOOLTIPS.futureProfit)}">ⓘ</span></div>
				<div class="pnl-card-value">${esc(fmt(vm.sales.installment.totalProfit))}</div>
				<div class="pnl-card-sub">${__(
					"Товарная маржа и потенциальные проценты по рассрочкам периода."
				)}</div>
			</div>
		`);
	}

	/* ——— Receipts→profit formula strip (§F, items 18-19) ———
	 * Reads collected/costRecovery/grossProfit straight from the view-model —
	 * grossProfit (= productMargin + interestIncome, per the adapter) is the
	 * value that makes the displayed identity always hold:
	 *   collected − costRecovery = grossProfit
	 * (costRecovery is itself defined as collected minus margin+interest, so
	 * using netProfit here — which additionally subtracts expenses — would
	 * make the displayed equation not add up whenever expenses are included).
	 */
	_renderFormula(vm) {
		const root = this.container.find(".pnl-section-recognized .pnl-formula").empty();
		const fmt = window.Nasiya365PnL.formatMoney;
		const esc = frappe.utils.escape_html;
		const r = vm.recognized;

		root.html(`
			<span class="pnl-formula-term">${__("Поступило")} <strong>${esc(fmt(r.collected))}</strong></span>
			<span class="pnl-formula-op">−</span>
			<span class="pnl-formula-term">${__("Покрытие себестоимости")} <strong>${esc(
			fmt(r.costRecovery)
		)}</strong></span>
			<span class="pnl-formula-op">=</span>
			<span class="pnl-formula-term pnl-formula-result">${__("Заработанная прибыль")} <strong>${esc(
			fmt(r.grossProfit)
		)}</strong></span>
		`);
	}

	/* ——— Table 1 «Продажи, оформленные за период» — наличные и рассрочка РАЗДЕЛЬНО (§6).
	 * Two transposed tables (metrics as rows) instead of one type-per-row table, so each
	 * type has its own total and the two are never summed into a combined «Итого». ——— */
	_renderSalesTable(vm) {
		const wrap = this.container.find(".pnl-section-sales .pnl-table-wrap").empty();
		const fmt = window.Nasiya365PnL.formatMoney;
		const esc = frappe.utils.escape_html;
		const c = vm.sales.cash;
		const i = vm.sales.installment;

		const cashRows = [[__("Выручка"), c.sales], [__("Себестоимость"), c.cost]];
		const instRows = [
			[__("Выручка"), i.sales],
			[__("Себестоимость"), i.cost],
			[__("Маржа товара"), i.margin],
			[__("Проценты по рассрочке"), i.interest],
		];
		// Pad the shorter table with blank rows so both TOTAL rows sit at the same level.
		const maxDataRows = Math.max(cashRows.length, instRows.length);

		const bodyHtml = (rows, total) => {
			const data = rows
				.map(
					([k, v]) =>
						`<tr><td class="pnl-col-label">${esc(k)}</td><td class="pnl-col-num">${esc(fmt(v))}</td></tr>`
				)
				.join("");
			const spacers = Array.from({ length: maxDataRows - rows.length })
				.map(
					() =>
						`<tr class="pnl-row-spacer"><td class="pnl-col-label">&nbsp;</td><td class="pnl-col-num"></td></tr>`
				)
				.join("");
			return (
				data +
				spacers +
				`<tr class="pnl-row-total"><td class="pnl-col-label">${esc(total[0])}</td><td class="pnl-col-num">${esc(fmt(total[1]))}</td></tr>`
			);
		};

		const tableHtml = (title, pillClass, pillText, rows, total) => `
			<div class="pnl-subtable">
				<div class="pnl-subtable-head">
					<span class="pnl-subtable-title">${esc(title)}</span>
					<span class="pnl-pill ${pillClass}">${esc(pillText)}</span>
				</div>
				<table class="pnl-table"><tbody>${bodyHtml(rows, total)}</tbody></table>
			</div>`;

		const cash = tableHtml(
			__("Наличные продажи"),
			"pnl-pill-cash",
			__("наличные"),
			cashRows,
			[__("Прибыль (маржа)"), c.totalProfit]
		);
		const inst = tableHtml(
			__("Продажи в рассрочку"),
			"pnl-pill-inst",
			__("рассрочка"),
			instRows,
			[__("Итого прибыль"), i.totalProfit]
		);

		wrap.html(`<div class="pnl-sales-split">${cash}${inst}</div>`);
	}

	/* ——— Table 2 «Поступления и заработанная прибыль» (§7) ——— */
	_recognizedRows(vm) {
		const r = vm.recognized;
		// Item 12/13: only call it "Заработанная прибыль за период" when expenses
		// are actually part of the total; otherwise name it by what it really is —
		// the total under whichever basis is configured (no "net profit" claim
		// unless expenses were subtracted).
		const totalLabel = vm.basis.expensesIncluded
			? __("Заработанная прибыль за период")
			: __("Прибыль по выбранной базе расчёта");
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
				tooltip: PNL_TOOLTIPS.earnedProfit,
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
				label: totalLabel,
				value: r.netProfit,
				explain: __("Итоговая прибыль по действующим настройкам"),
				bold: true,
				tooltip: PNL_TOOLTIPS.totalRow,
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
					? `${esc(row.label)} <span class="pnl-info-icon" title="${esc(row.tooltip)}">ⓘ</span>`
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

	/* ——— Info block (§B, items 9-11) ———
	 * Always shown (not only when something is excluded) so the settings are
	 * visible regardless of the configured basis, and always lists all three
	 * inclusion lines so there's no contradiction like "Чистая прибыль" +
	 * "expenses not included".
	 */
	_basisLine(profitBasis) {
		if (profitBasis === "Только маржа") {
			return __("База распределения: признанная товарная маржа");
		}
		if (profitBasis === "Валовая прибыль") {
			return __("База распределения: товарная маржа + проценты");
		}
		if (profitBasis === "Чистая прибыль") {
			return __("База распределения: чистая прибыль (маржа + проценты − расходы)");
		}
		return `${__("База распределения")}: ${profitBasis || "—"}`;
	}

	_renderInfoBlock(vm) {
		const root = this.container.find(".pnl-info-block").empty();
		const esc = frappe.utils.escape_html;
		const b = vm.basis;

		const lines = [
			esc(this._basisLine(b.profitBasis)),
			esc(__("Товарная маржа: входит в итог")),
			esc(
				b.interestIncluded
					? __("Процентный доход: входит в итог")
					: __("Процентный доход: не входит в итог")
			),
			esc(
				b.expensesIncluded
					? __("Операционные расходы: входят в итог")
					: __("Операционные расходы: не входят в итог")
			),
		];

		root.html(`
			<div class="pnl-basis-info">
				<div class="pnl-basis-info-title">${esc(__("Настройки расчёта и распределения прибыли"))}</div>
				${lines.map((l) => `<div>${l}</div>`).join("")}
			</div>
		`);
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
