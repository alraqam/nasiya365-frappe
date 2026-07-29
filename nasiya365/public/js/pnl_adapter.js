/**
 * Pure view-model adapter for the P&L (Прибыль и поступления) report.
 *
 * No Frappe/$/window/DOM references — these functions are deterministic and
 * unit-tested in isolation via Node (see nasiya365/tests_js/pnl_adapter.test.js),
 * mirroring the rationale in nasiya365/api/recognition.py. They perform NO
 * financial logic beyond the derivations documented in the P&L redesign plan
 * (docs/superpowers/plans/2026-07-28-pnl-report-redesign.md, Task 1): pure
 * key-remapping/renaming plus the one documented cost-recovery formula.
 *
 * Backend source of truth: nasiya365.api.profit.get_profit_summary (unchanged).
 * The cost-recovery dict has TWO same-concept key sets:
 *   - Table 1 (sales, by deal type as booked) uses the `sales_*` keys.
 *   - Table 2 (recognized/collected) uses the BARE `cash_margin` /
 *     `financed_margin` / `interest_income` keys — these are what has actually
 *     been recognized under cost-recovery, and must NOT be confused with the
 *     `sales_*` figures.
 */

/** Null-safe numeric coercion: non-finite/non-number inputs resolve to 0. */
function num(x) {
  return typeof x === "number" && isFinite(x) ? x : 0;
}

/**
 * Transform the raw get_profit_summary() response into a render-ready
 * view-model. Pure; does not mutate `raw`.
 */
function buildViewModel(raw) {
  raw = raw || {};

  const salesCashRevenue = num(raw.sales_cash_revenue);
  const salesCashCogs = num(raw.sales_cash_cogs);
  const salesCashMargin = num(raw.sales_cash_margin);

  const salesFinancedRevenue = num(raw.sales_financed_revenue);
  const salesFinancedCogs = num(raw.sales_financed_cogs);
  const salesFinancedMargin = num(raw.sales_financed_margin);
  const salesInterest = num(raw.sales_interest);

  const salesTotalMargin = num(raw.sales_total_margin);
  const potentialProfit = num(raw.potential_profit);

  const collected = num(raw.collected);
  const cashMargin = num(raw.cash_margin);
  const financedMargin = num(raw.financed_margin);
  const interestIncome = num(raw.interest_income);

  return {
    summary: {
      collected: collected,
      earnedProfit: num(raw.net_profit),
      futureProfit: potentialProfit,
    },
    sales: {
      cash: {
        sales: salesCashRevenue,
        cost: salesCashCogs,
        margin: salesCashMargin,
        interest: null, // cash sales have no interest — renders "—"
        totalProfit: salesCashMargin,
      },
      installment: {
        sales: salesFinancedRevenue,
        cost: salesFinancedCogs,
        margin: salesFinancedMargin,
        interest: salesInterest,
        totalProfit: salesFinancedMargin + salesInterest,
      },
      total: {
        sales: salesCashRevenue + salesFinancedRevenue,
        cost: salesCashCogs + salesFinancedCogs,
        margin: salesTotalMargin,
        interest: salesInterest,
        totalProfit: potentialProfit,
      },
    },
    recognized: {
      collected: collected,
      costRecovery: collected - (cashMargin + financedMargin + interestIncome),
      productMargin: cashMargin + financedMargin,
      interestIncome: interestIncome,
      grossProfit: num(raw.gross_profit),
      operatingExpenses: num(raw.expenses),
      netProfit: num(raw.net_profit),
    },
    // Basis-driven (NOT value-driven): whether interest/expenses are part of
    // the distributed total depends on the merchant's configured
    // `profit_basis`, not on whether this period happens to have a zero
    // amount for them. (The backend's `interest_in_profit`/`expenses_in_profit`
    // flags go falsy whenever the period's amount is 0, which produces a
    // contradictory label — e.g. profit_basis "Чистая прибыль" with $0
    // expenses this period would otherwise read as "expenses not included".)
    basis: {
      profitBasis: raw.profit_basis,
      interestIncluded: raw.profit_basis !== "Только маржа",
      expensesIncluded: raw.profit_basis === "Чистая прибыль",
    },
  };
}

/**
 * Format a numeric (or null/undefined) value as money for display.
 *   - null/undefined            -> "—"           (em dash: genuinely absent)
 *   - 0                         -> "$0.00"
 *   - positive                  -> "$3 510.00"    (space thousands separator)
 *   - negative                  -> "−$120.00"     (U+2212 real minus, absolute value)
 * Right-alignment is a CSS concern for the renderer, not this function.
 */
function formatMoney(value) {
  if (value === null || value === undefined) {
    return "—";
  }

  const n = num(value);
  const isNegative = n < 0;
  const abs = Math.abs(n);

  const fixed = abs.toFixed(2);
  const parts = fixed.split(".");
  const intPart = parts[0];
  const decimalPart = parts[1];

  const withSeparators = intPart.replace(/\B(?=(\d{3})+(?!\d))/g, " ");

  return (isNegative ? "−$" : "$") + withSeparators + "." + decimalPart;
}

if (typeof window !== "undefined") {
  window.Nasiya365PnL = { buildViewModel, formatMoney };
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { buildViewModel, formatMoney };
}
