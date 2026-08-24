const assert = require("assert");
const { buildViewModel, formatMoney } = require("../public/js/pnl_adapter.js");

// §16 backend sample (cost-recovery). COGS values are POSITIVE magnitudes as the backend returns them.
const raw = {
  sales_cash_revenue: 850, sales_cash_cogs: 800, sales_cash_margin: 50,
  sales_financed_revenue: 2660, sales_financed_cogs: 2615, sales_financed_margin: 45,
  sales_total_margin: 95, sales_interest: 508, potential_profit: 603,
  collected: 1840, cash_margin: 50, financed_margin: 0, interest_income: 0,
  gross_profit: 50, net_profit: 50, expenses: 0,
  interest_in_profit: 0, expenses_in_profit: 1, profit_basis: "Чистая прибыль",
  profit_method: "Возмещение затрат",
};
const vm = buildViewModel(raw);

// 1. transform correctness (summary + both tables)
assert.strictEqual(vm.summary.collected, 1840);
assert.strictEqual(vm.summary.earnedProfit, 50);
assert.strictEqual(vm.summary.futureProfit, 603);
assert.strictEqual(vm.sales.cash.totalProfit, 50);
assert.strictEqual(vm.sales.installment.totalProfit, 553); // 45 + 508
assert.strictEqual(vm.sales.total.sales, 3510);
assert.strictEqual(vm.sales.total.cost, 3415);
assert.strictEqual(vm.sales.total.totalProfit, 603);
assert.strictEqual(vm.recognized.costRecovery, 1790); // 1840 - (50+0+0)
assert.strictEqual(vm.recognized.productMargin, 50);
assert.strictEqual(vm.recognized.netProfit, 50);
// 2. cost stays positive
assert.strictEqual(vm.sales.cash.cost, 800);
assert.strictEqual(vm.sales.installment.cost, 2615);
// 3. cash interest is null (renders em dash)
assert.strictEqual(vm.sales.cash.interest, null);
// 4. zero interest for installment renders $0.00 (value stays 0, not null)
assert.strictEqual(vm.sales.installment.interest, 508);
assert.strictEqual(formatMoney(0), "$0.00");
// 5. missing/undefined input does not throw and yields 0
const vm2 = buildViewModel({});
assert.strictEqual(vm2.summary.collected, 0);
assert.strictEqual(vm2.recognized.costRecovery, 0);
// 6. formatter
assert.strictEqual(formatMoney(null), "—");
assert.strictEqual(formatMoney(3510), "$3 510.00");
assert.strictEqual(formatMoney(-120), "−$120.00");
assert.strictEqual(formatMoney(95), "$95.00");

// 7. basis flags are BASIS-driven (profit_basis), not value-driven — this sample's
// profit_basis is "Чистая прибыль" so both are true even though the raw
// interest_in_profit=0/expenses_in_profit=1 flags (now unused) would suggest otherwise.
assert.strictEqual(vm.basis.profitBasis, "Чистая прибыль");
assert.strictEqual(vm.basis.interestIncluded, true);
assert.strictEqual(typeof vm.basis.interestIncluded, "boolean");
assert.strictEqual(vm.basis.expensesIncluded, true);
assert.strictEqual(typeof vm.basis.expensesIncluded, "boolean");

// 8. recognized gross profit / operating expenses (§16 sample)
assert.strictEqual(vm.recognized.grossProfit, 50);
assert.strictEqual(vm.recognized.operatingExpenses, 0);

// 9. sales totals (§16 sample)
assert.strictEqual(vm.sales.total.margin, 95);
assert.strictEqual(vm.sales.total.interest, 508);

// 10. a LITERAL zero installment interest stays numeric 0 (renders $0.00), not null
const rawZeroInterest = Object.assign({}, raw, { sales_interest: 0 });
const vmZeroInterest = buildViewModel(rawZeroInterest);
assert.strictEqual(vmZeroInterest.sales.installment.interest, 0);
assert.notStrictEqual(vmZeroInterest.sales.installment.interest, null);
assert.strictEqual(formatMoney(vmZeroInterest.sales.installment.interest), "$0.00");

// 11. basis flags for all three profit_basis values (adapter change §A) — derived from
// profit_basis alone, independent of the period's interest/expenses amounts.

// "Только маржа" -> both false (interest excluded, expenses excluded)
const vmMarginOnly = buildViewModel(Object.assign({}, raw, { profit_basis: "Только маржа" }));
assert.strictEqual(vmMarginOnly.basis.interestIncluded, false);
assert.strictEqual(vmMarginOnly.basis.expensesIncluded, false);

// "Валовая прибыль" -> interest included, expenses excluded
const vmGross = buildViewModel(Object.assign({}, raw, { profit_basis: "Валовая прибыль" }));
assert.strictEqual(vmGross.basis.interestIncluded, true);
assert.strictEqual(vmGross.basis.expensesIncluded, false);

// "Чистая прибыль" -> both true, even when this period's interest/expenses amounts are 0
// (raw already has interest_income: 0, expenses: 0 — see vm.basis assertions in §7 above).
const vmNet = buildViewModel(Object.assign({}, raw, { profit_basis: "Чистая прибыль", interest_income: 0, expenses: 0 }));
assert.strictEqual(vmNet.basis.interestIncluded, true);
assert.strictEqual(vmNet.basis.expensesIncluded, true);

// 12. авансы (down_payment) прокидываются в installment для справочной строки
//     под наличными; totalProfit (карточка) остаётся маржа+проценты и не меняется.
const rawDp = Object.assign({}, raw, { sales_down_payment: 300 });
const vmDp = buildViewModel(rawDp);
assert.strictEqual(vmDp.sales.installment.downPayment, 300);
assert.strictEqual(vmDp.sales.installment.totalProfit, 553); // 45 + 508, без изменений
assert.strictEqual(buildViewModel({}).sales.installment.downPayment, 0);

console.log("ALL PNL ADAPTER TESTS PASSED");
