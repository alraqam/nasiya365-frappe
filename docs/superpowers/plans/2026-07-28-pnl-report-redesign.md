# P&L Report Redesign — Implementation Plan

> **For agentic workers:** implement task-by-task, TDD. Steps use `- [ ]`. Backend/formulas are FROZEN.

**Goal:** Presentation-only redesign of the P&L report as a new custom Desk Page «Прибыль и поступления» — top cards + two clean tables (Продажи / Поступления) + info block + tooltips + loading/empty/error states + unified money format + responsive + CSV export — driven by a pure client-side adapter over the UNCHANGED `get_profit_summary` backend.

**Architecture:** New Frappe custom Page (mirrors `bnpl_control_center`). Pure Frappe-free adapter `public/js/pnl_adapter.js` (view-model + money formatter), node-tested, loaded by the page via `frappe.require` from the raw `/assets/` path (works on dev AND prod without a bundle rebuild). Page CSS as a raw `public/css/pnl.css` loaded via injected `<link>`. Backend reused as-is via `nasiya365.api.profit.get_profit_summary`.

**Tech:** Frappe v16 Desk Page (jQuery + template literals, no framework), plain JS adapter, Node built-in `assert` for tests.

## Global Constraints (from task spec — verbatim intent)
- **DO NOT** change: profit formulas, recognition method, profit basis, SQL, DB schema, existing documents/transactions. No new financial accounts. No Balans/HISOB. Do not merge potential vs recognized profit.
- Reuse `get_profit_summary(from_date, to_date, branch=None)` unchanged. The adapter performs NO financial logic beyond the exact derivations named below.
- Page roles MUST be set explicitly to `System Manager` + `Nasiya365 Admin` (else access silently widens — the endpoint has no role gate).
- Money format: right-aligned, 2 decimals, **space** thousands separator, `$0.00` for zero, `—` (em dash) for a genuinely absent value, `−$120.00` for negative (real minus sign U+2212). One shared formatter.
- Cost-recovery dict has TWO same-concept key sets: **Table 1 uses `sales_*` keys; Table 2 uses the bare recognized `cash_margin`/`financed_margin`/`interest_income`/`total_margin`.** See foundation brief §B3/§D.
- Keep export, filters, refresh (re-implemented on the page). Leave the old "Profit and Loss Summary" Script Report registered (untouched) as a fallback.

---

### Task 1: Pure adapter + money formatter + Node tests (TDD)

**Files:**
- Create: `nasiya365/public/js/pnl_adapter.js`
- Test: `nasiya365/tests_js/pnl_adapter.test.js`

**Interfaces (produces):**
- `buildViewModel(raw)` → view-model object (shape below). Pure; no frappe/DOM/window.
- `formatMoney(value)` → string. Pure.
- Dual export: browser global `window.Nasiya365PnL = { buildViewModel, formatMoney }` AND `if (typeof module !== "undefined" && module.exports) module.exports = { buildViewModel, formatMoney }`.

**View-model shape** (numbers are raw JS numbers; the renderer formats later):
```js
{
  summary: { collected, earnedProfit, futureProfit },
  sales: {
    cash:        { sales, cost, margin, interest: null, totalProfit },
    installment: { sales, cost, margin, interest, totalProfit },
    total:       { sales, cost, margin, interest, totalProfit },
  },
  recognized: { collected, costRecovery, productMargin, interestIncome, grossProfit, operatingExpenses, netProfit },
  basis: { profitBasis, interestInProfit, expensesInProfit },
}
```

**Exact mapping (from foundation brief §D — do NOT invent other math):**
- `summary.collected` = `collected`
- `summary.earnedProfit` = `net_profit`
- `summary.futureProfit` = `potential_profit`
- `sales.cash.sales` = `sales_cash_revenue`; `.cost` = `sales_cash_cogs` (already positive, keep positive); `.margin` = `sales_cash_margin`; `.interest` = `null` (cash has NO interest → renders `—`); `.totalProfit` = `sales_cash_margin`
- `sales.installment.sales` = `sales_financed_revenue`; `.cost` = `sales_financed_cogs`; `.margin` = `sales_financed_margin`; `.interest` = `sales_interest`; `.totalProfit` = `sales_financed_margin + sales_interest`
- `sales.total.sales` = `sales_cash_revenue + sales_financed_revenue`; `.cost` = `sales_cash_cogs + sales_financed_cogs`; `.margin` = `sales_total_margin`; `.interest` = `sales_interest`; `.totalProfit` = `potential_profit`
- `recognized.collected` = `collected`
- `recognized.costRecovery` = `collected − (cash_margin + financed_margin + interest_income)` **(this exact formula — NOT `gross_profit`)**; display positive
- `recognized.productMargin` = `cash_margin + financed_margin`
- `recognized.interestIncome` = `interest_income`
- `recognized.grossProfit` = `gross_profit`
- `recognized.operatingExpenses` = `expenses`
- `recognized.netProfit` = `net_profit`
- `basis.profitBasis` = `profit_basis`; `.interestInProfit` = `!!interest_in_profit`; `.expensesInProfit` = `!!expenses_in_profit`

**Null-safety:** a helper `num(x)` → `(typeof x === "number" && isFinite(x)) ? x : 0`. Apply to every backend read EXCEPT cash interest (which is intentionally `null`). Missing/undefined/null numeric inputs must not throw and must resolve to `0`.

**`formatMoney(value)` rules:**
- `value === null` or `undefined` → `"—"`.
- else coerce via `num`; format with 2 decimals, space (U+0020, or non-breaking — use a regular space) as thousands separator, `$` prefix.
- zero → `"$0.00"`. negative → `"−$120.00"` (U+2212 minus, `$`, absolute value). positive → `"$3 510.00"`.
- (Right-alignment is CSS in the renderer, not this function.)

- [ ] **Step 1: Write the failing tests** — `nasiya365/tests_js/pnl_adapter.test.js`, using Node's `assert`. Use the spec §16 test data as `raw` input:
```js
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

console.log("ALL PNL ADAPTER TESTS PASSED");
```

- [ ] **Step 2: Run, verify it fails** — `node nasiya365/tests_js/pnl_adapter.test.js` → fails (module not found / assertions).
- [ ] **Step 3: Implement `nasiya365/public/js/pnl_adapter.js`** per the mapping + rules above. Pure, dual-export. Include a file header comment (no frappe imports; testable via node), mirroring `nasiya365/api/recognition.py` rationale.
- [ ] **Step 4: Run tests, verify pass** — `node nasiya365/tests_js/pnl_adapter.test.js` → `ALL PNL ADAPTER TESTS PASSED`.
- [ ] **Step 5: Commit** — `git add nasiya365/public/js/pnl_adapter.js nasiya365/tests_js/pnl_adapter.test.js && git commit -m "feat(pnl-ui): pure view-model adapter + money formatter + node tests"`

---

### Task 2: Page scaffold (record + boilerplate) + roles + workspace link

**Files:**
- Create: `nasiya365/nasiya365/page/profit_and_loss/__init__.py` (empty + copyright header, copy from `bnpl_control_center/__init__.py`)
- Create: `nasiya365/nasiya365/page/profit_and_loss/profit_and_loss.json`
- Create: `nasiya365/nasiya365/page/profit_and_loss/profit_and_loss.py` (copy `bnpl_control_center.py` `get_context` boilerplate)
- Modify: `nasiya365/nasiya365/workspace/nasiya365/nasiya365.json` (repoint the P&L link + shortcut to the new Page)

- [ ] **Step 1: Create the page record** `profit_and_loss.json` (mirror `bnpl_control_center.json` but set roles):
```json
{
    "content": null, "creation": "2026-07-28 00:00:00.000000", "docstatus": 0,
    "doctype": "Page", "idx": 0, "modified": "2026-07-28 00:00:00.000000",
    "modified_by": "Administrator", "module": "nasiya365",
    "name": "profit-and-loss", "owner": "Administrator", "page_name": "profit-and-loss",
    "roles": [{"role": "System Manager"}, {"role": "Nasiya365 Admin"}],
    "script": "", "standard": "Yes", "style": "", "system_page": 0,
    "title": "Прибыль и поступления"
}
```
- [ ] **Step 2: Create `profit_and_loss.py` + `__init__.py`** copying the exact boilerplate from `bnpl_control_center` (get_context only).
- [ ] **Step 3: Repoint workspace** — in `nasiya365/nasiya365/workspace/nasiya365/nasiya365.json`, find the link (`link_type: "Report"`, `link_to: "Profit and Loss Summary"`, ~line 172) and the shortcut (`type: "Report"`, ~line 296). Change the LINK to `"link_type": "Page", "link_to": "profit-and-loss"` (drop `is_query_report`), and the SHORTCUT to `"type": "Page", "link_to": "profit-and-loss"` (drop `report_ref_doctype`). Keep the label «Прибыль и убытки»→ rename label to «Прибыль и поступления». Do not remove other links. Validate JSON parses.
- [ ] **Step 4: Verify JSON parses** — `python3 -c "import json; json.load(open('nasiya365/nasiya365/page/profit_and_loss/profit_and_loss.json')); json.load(open('nasiya365/nasiya365/workspace/nasiya365/nasiya365.json')); print('OK')"`
- [ ] **Step 5: Commit** — `git commit -m "feat(pnl-ui): register profit-and-loss page (roles set) + repoint workspace link"`

---

### Task 3: Page JS (render) + CSS

**Files:**
- Create: `nasiya365/nasiya365/page/profit_and_loss/profit_and_loss.js`
- Create: `nasiya365/public/css/pnl.css`

**Consumes:** `Nasiya365PnL.buildViewModel` / `Nasiya365PnL.formatMoney` (Task 1), `get_profit_summary` endpoint.

**Requirements (implement all; spec sections in parens):**
- `frappe.pages["profit-and-loss"].on_page_load(wrapper)` → `frappe.ui.make_app_page({parent, title: "Прибыль и поступления", single_column: true})`.
- On load: inject `<link rel="stylesheet" href="/assets/nasiya365/css/pnl.css">` into `<head>` once (guard by id). Then `frappe.require("/assets/nasiya365/js/pnl_adapter.js", () => { ...init... })` so `Nasiya365PnL` is available.
- **Header** (§3): title «Прибыль и поступления»; subtitle two lines ("Продажи учитываются по дате оформления сделки." / "Поступления учитываются по дате фактического получения денег."); a method line «Метод признания прибыли: возмещение себестоимости» with a tooltip (title attr) «Прибыль по сделке признаётся после того, как полученные платежи покроют себестоимость проданного товара.»
- **Filters** (§4): `Дата с` (Date, default today−1 month), `Дата по` (Date, default today), `Филиал` (Link → Branch, optional), a button «Сформировать отчёт». Use `frappe.ui.form.make_control` for the fields (Date/Link), or `page.add_field`. One row on wide screens, wrap on narrow (flex-wrap). Also a «Обновить» button + an «Обновлено: DD.MM.YYYY, HH:MM» label (§13) updated to `frappe.datetime` now on each successful load. Keep an «Экспорт CSV» button (§14 — don't remove export).
- **Data flow:** on «Сформировать отчёт»/«Обновить» (and once on load) → show loading state → `frappe.call({ method: "nasiya365.api.profit.get_profit_summary", args: {from_date, to_date, branch} })` → on `r.exc` show error state → else `vm = Nasiya365PnL.buildViewModel(r.message)` → render cards + tables + info block; set «Обновлено» to now. Never leave stale numbers while loading (§12).
- **Cards** (§5): 3 cards — «Поступило от клиентов» = `vm.summary.collected` (sub: "Все фактически полученные платежи за выбранный период"); «Заработанная прибыль» = `vm.summary.earnedProfit` (sub: "Прибыль, признанная по фактически полученным платежам"; green ONLY when > 0, §10.7); «Будущая прибыль по новым сделкам» = `vm.summary.futureProfit` (sub: "При условии полной оплаты всех новых рассрочек"; visually DISTINCT from earned — muted/outlined, NOT green, §5+§10.8; tooltip §11). Use `formatMoney`.
- **Table 1 «1. Продажи, оформленные за период»** (§6): description line; columns [Тип продажи | Продажи | Себестоимость | Маржа товара | Проценты по рассрочке | Общая прибыль]; rows Наличные / Рассрочка / **Итого** (bold). Values from `vm.sales.{cash,installment,total}` via `formatMoney`. Cash «Проценты» cell = `—` (because `interest === null`); installment/total show `formatMoney(interest)` (so 0 → `$0.00`). Cost shown positive.
- **Table 2 «2. Поступления и заработанная прибыль»** (§7): description; columns [Показатель | Сумма | Объяснение]; rows Поступило от клиентов / Покрытие себестоимости / Заработанная товарная маржа / Заработанные проценты / **Валовая прибыль** (bold) / Операционные расходы / **Заработанная прибыль за период** (bold), from `vm.recognized`. «Покрытие себестоимости» positive. Explanations from §7 table. Tooltips on «Покрытие себестоимости», «Заработанная товарная маржа», «Заработанные проценты», «Заработанная прибыль» (§11).
- **Info block** (§8): if `!vm.basis.interestInProfit` OR `!vm.basis.expensesInProfit`, show a subtle info block: «База расчёта прибыли: {profitBasis}», and lines «Процентный доход: не входит в итог» / «Операционные расходы: не входят в итог» conditionally. Do not hide the rows themselves.
- **States** (§12): loading (a `.pnl-skeleton` or `frappe.dom.freeze`), empty («За выбранный период продажи и платежи не найдены. Измените даты или выберите другой филиал.» when all of collected/potential/sales are 0), error («Не удалось сформировать отчёт. Повторите попытку или обратитесь к администратору.»; log the technical error via `console.error`).
- **Export CSV** (§14): builds a CSV client-side from the two tables' current values, triggers a download (Blob + `<a download>`), filename like `pribyl-i-postupleniya-{from}-{to}.csv`.
- **Visuals** (§10): full width; no row numbers; no truncation (no ellipsis on labels); bold totals; spacing between sections; minimal colors; green ONLY for actual positive earned profit; potential not green; numbers right-aligned; horizontal scroll for tables on narrow screens (`overflow-x:auto` wrapper); no empty right gap; match app style; no new UI library. Remove the «>5 <10 =324» hint / RU-EN mix / dev names (N/A on a fresh page — just don't introduce them).

- [ ] **Step 1: Write `public/css/pnl.css`** — `.pnl-*` classes: layout (full-width container, cards grid, tables), right-aligned numeric cells (`text-align:right; font-variant-numeric: tabular-nums`), bold totals, section spacing, muted/outlined future-profit card, green class used ONLY on positive earned profit, `overflow-x:auto` table wrappers, skeleton/loading + empty + error styles, responsive (`@media` wrap filters, scroll tables). Follow the `.bnpl-*` visual style. Theme-aware if the app is (check existing bundle for dark handling; otherwise match app defaults).
- [ ] **Step 2: Write `page/profit_and_loss/profit_and_loss.js`** implementing all requirements above (adapter via frappe.require; cards; two tables; info block; states; filters; refresh; timestamp; export; tooltips).
- [ ] **Step 3: Restart dev backend so the new page/py registers** — `docker restart nasiya365-frappe-backend-1` and wait for readiness (`bench --site my.nasiya365.uz execute frappe.utils.now`). (Page JS + raw CSS/adapter are served raw; no bundle build needed.)
- [ ] **Step 4: Smoke-load in the container** — `docker exec nasiya365-frappe-backend-1 bench --site my.nasiya365.uz execute frappe.get_all --kwargs "{'doctype':'Page','filters':{'name':'profit-and-loss'}}"` returns the page (proves the record synced after a `bench migrate`; if empty, run `bench --site my.nasiya365.uz migrate` first, then re-check).
- [ ] **Step 5: Commit** — `git commit -m "feat(pnl-ui): profit-and-loss page render (cards, 2 tables, states, export) + css"`

---

### Task 4: Dev verification (browser) + full test run + §20 report

**No new files** (verification + a report doc). This runs under the controller's browser control; the implementer prepares commands + the report.

- [ ] **Step 1: Node adapter tests** — `node nasiya365/tests_js/pnl_adapter.test.js` → PASS.
- [ ] **Step 2: Full Python suite unaffected** — `docker exec nasiya365-frappe-backend-1 bench --site my.nasiya365.uz run-tests --app nasiya365` → all pass (proves backend untouched).
- [ ] **Step 3: Browser verify on dev** (controller does this): open `http://localhost:8081/app/profit-and-loss`; verify — page loads with new title; cards render (future-profit visually distinct, not green; earned green only if >0); Table 1 (Наличные/Рассрочка/Итого, cost positive, cash interest `—`); Table 2 (Покрытие себестоимости positive); money format (`$3 510.00`, `—`, `−$…`); change dates/branch → «Сформировать отчёт» updates everything + «Обновлено» timestamp changes; empty state on a no-data period; export downloads a CSV; responsive (narrow → tables scroll, filters wrap); no row numbers, no truncation, no empty right gap. Screenshot.
- [ ] **Step 4: Write §20 report** to `docs/pnl-redesign-report.md`: files changed, components/functions created, confirmation backend formulas unchanged (diff shows profit.py untouched), test results, manual-check results, screenshot path, known limitations (raw non-hashed assets → note cache-busting to fold into the bundle at prod deploy time), confirmation Balans/HISOB not added.
- [ ] **Step 5: Commit** the report doc.

---

## Self-review
- Spec coverage: §3 header (T3), §4 filters (T3), §5 cards (T3), §6 Table1 (T1 vm + T3 render), §7 Table2 (T1 vm + T3 render), §8 info block (T3), §9 formatMoney (T1), §10 visuals (T3 css), §11 tooltips (T3), §12 states (T3), §13 timestamp+refresh (T3), §14 remove cruft + keep export (T3), §15 adapter (T1), §16 test data (T1 tests), §17 tests (T1 + T4), §18 acceptance (T4 verify), §20 report (T4). ✅
- Constraints: backend frozen (only new files + workspace link + css; profit.py untouched — T4 step 2 proves it). Roles set (T2). No Balans/HISOB. ✅
- Known deviation (documented): adapter/CSS served as raw `/assets` files (not content-hashed) for dev-buildability; fold into the hashed bundle when the user deploys. Noted in T4 report.
