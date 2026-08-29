# P&L / Reports correctness audit — deal lifecycle × report logic

**Date:** 2026-07-29
**Status:** investigation only — no code or data changed
**Trigger:** merchant reports that the financial reports "show and calculate incorrectly"
**Scope:** `nasiya365/api/profit.py` + 4 reports (`shareholder_distribution`, `sales_report`,
`collections_and_overdue`, `suppliers_payable`) × the full BNPL deal lifecycle (Sales Order,
Installment Plan, Payment Transaction, Trade In, Stock Entry, Expense, Cashbox family).

This doc is read-only research. It does **not** propose code fixes — it maps the data model,
maps each report's exact formula, and flags concrete, evidence-backed scenarios where a report's
number will diverge from what actually happened. Section D turns those into a seedable test matrix.

Related prior design docs (do **not** re-litigate what these already decided intentionally):
- `docs/superpowers/specs/2026-07-28-pnl-cost-recovery-design.md` — cost-recovery method design;
  explicitly says Раздел 1 (sales/potential) and Раздел 2 (recognized/collected) are **allowed to
  disagree** — that's by design, not a bug.
- `docs/superpowers/specs/2026-07-28-pnl-redesign-foundation.md` — confirms P&L page has no role
  gate of its own beyond `roles: [System Manager, Nasiya365 Admin]`, branch filtering is row-level.

---

## TL;DR — top 3 suspects, ranked

1. **[CRITICAL] Branch filter silently drops all new-style installment sales.** Every report's
   branch resolution for Installment-Plan-backed data goes `ip.sales_order → Sales Order.branch`.
   But `Installment Plan.sales_order` is a **hidden, legacy/import-only field** — the current
   plan-creation flow (via `stock_entry`) never sets it. So the moment anyone — including a System
   Manager, the only role allowed to open these reports — filters P&L / Sales Report /
   Collections & Overdue by a specific branch, **every installment-plan deal created through the
   normal current workflow vanishes** from that branch's numbers (revenue, margin, interest,
   collections, everything). "All branches" (no filter) is unaffected. See C1.

2. **[HIGH] Cancelling a cash sale doesn't cancel its Payment Transaction.** `Sales Order.on_cancel`
   reverses stock but never touches the auto-created Payment Transaction from
   `create_cash_receipt()`. A returned/refunded cash sale keeps recognizing revenue+margin+profit
   forever in `_compute_cash` / `_compute_cost_recovery`, while the Sales Report (which does check
   `so.docstatus=1`) correctly drops it — so P&L and Sales Report **disagree** on the same
   returned sale. See C2.

3. **[MEDIUM-HIGH] No refund/return mechanism exists at all**, and the one that's half-designed in
   the code (negative-amount Payment Transaction) is unreachable: `Payment Transaction.validate()`
   throws on total ≤ 0 and silently skips any row ≤ 0. The comment in `_compute_cost_recovery`
   claiming refunds "flow through" describes dead code. The *only* working reversal path today is
   cancelling the entire Installment Plan (all-or-nothing) or cancelling the whole cash Sales Order
   (which itself is broken per #2). See C3/C4.

---

## A. Deal lifecycle & data model

### A1. Cash sale — «Продажа наличная»

**Doctype:** `Sales Order` (`nasiya365/nasiya365/doctype/sales_order/sales_order.py`,
`sales_order.json`).

- `status` options: `Черновик` (Draft) → `Подтвержден` (Confirmed, on submit) → `Доставлен`
  (Delivered) → `Отменен` (Cancelled). `docstatus` 0/1/2 as usual.
- `before_submit` (line 24-35): **hard-blocks submission unless `balance_amount <= 0`** — i.e. a
  cash sale must be fully paid before it can even become a real document. This is the structural
  definition of "cash sale" vs "installment sale" (`profit.py:24-25` docstring: *"Cash sale =
  Sales Order (docstatus=1) NOT referenced by any Installment Plan"*).
- `on_submit` (line 37-43): sets `status="Подтвержден"`, calls `update_stock()` (writes negative
  `Stock Ledger` rows), then `create_cash_receipt()` (line 230-285) — **auto-creates and submits
  a `Payment Transaction`** for the full `paid_amount` (or split `payment_lines`), with
  `reference_doctype="Sales Order"`.
- `on_cancel` (line 45-48): sets `status="Отменен"`, calls `reverse_stock()` (writes positive
  `Stock Ledger` rows to restore quantity). **Does NOT touch the Payment Transaction created in
  `create_cash_receipt()`.** See C2.
- `branch` is a **required, direct field** on Sales Order (`sales_order.json:85-92`) — cash sales
  are always branch-resolvable, unlike installment plans (see A2).
- Trade-in can fund a cash sale as part-payment via `Sales Order.trade_in` (see A5).

### A2. Installment sale — «Продажа в рассрочку»

**Doctype:** `Installment Plan` (`installment_plan.json`, `installment_plan.py`).

**`status` options** (`installment_plan.json:119-126`, Select field, default `Черновик`):
| Value | Meaning | Set by |
|---|---|---|
| `Черновик` | Draft, docstatus=0, freely editable | default |
| `Активный` | Live/submitted | `on_submit` (`installment_plan.py:220-223`) |
| `Завершен` | Fully paid — every schedule row `Оплачен` | `apply_payment` (`installment_plan.py:731-732`) when `all(s.status=="Оплачен")` |
| `Просрочен` | Has ≥1 overdue schedule row | daily scheduled job `tasks/daily.py:19-30` (bulk SQL), and `install.py:1097-1122` (recompute utility) |
| `Списан` | Written off (bad debt) | **never set programmatically anywhere in the codebase** — manual/admin-only field edit |

**`contract_status` options** (`installment_plan.json:350-356`, default `Не подписан`):
`Не подписан` (unsigned) → `Подписан` (signed, auto-set in `set_contract_fields_from_plan`,
`installment_plan.py:189-192`, when both `signed_by_customer` and `signed_by_merchant` are true)
→ `Отменен` (cancelled). **`Отменен` is never written by any code path** — confirmed by
exhaustive grep for `contract_status\s*=\s*['"]Отменен` and `db_set("contract_status"` across the
whole app: zero matches. See C3 — this makes every `contract_status != 'Отменен'` guard in the
report/profit code dead code.

**Submission / cancellation state machine** (docstring at `installment_plan.py:58-72`, verified
against the code):
- `on_submit` (line 220-223): `status → Активный`, `update_customer_limit()`, `create_contract()`
  (auto-creates a `Contract` doc, `contract.py:130-152`).
- `before_cancel` (line 225-238) → `on_cancel` (line 267-278): cascades `.cancel()` on every
  `docstatus=1` Payment Transaction referencing this plan (`_cascade_cancel_linked_payments`,
  line 303-332), cancels/deletes the linked `Contract`, refreshes the linked Stock Entry's
  `business_status` back to "available" (`_refresh_linked_stock_entry_status` →
  `stock_entry.py: refresh_stock_entry_business_status`), sets `status="Отменен"`, releases the
  customer's credit limit. **This is the one place cancellation is handled correctly end-to-end**
  (PTs really do get `docstatus=2`).
- `on_trash`: blocked entirely if any Payment Transaction is linked.

**Payment schedule + down payment** (`installment_plan.py:475-617`, `generate_schedule`):
- `financed_amount = principal_amount - down_payment`; `total_interest = financed_amount ×
  interest_rate × number_of_installments` (flat, not amortized/declining-balance).
- `total_amount = financed_amount + total_interest + down_payment` (down payment only added when
  a "row 0" schedule row exists for it).
- Schedule row 0 (`installment_number=0`) is the down payment, due **today** (contract signing
  date), not the first installment date (line 564-575) — this exists specifically so the "due
  today" dashboard panel tracks it.
- Regular rows 1..N are evenly split (`installment_amount = financed_total / N`), with the **last
  row absorbing the rounding remainder** so `Σ schedule.amount == total_amount` exactly
  (line 584-589).
- `apply_payment(amount, ...)` (line 653-753): allocates a payment to the **oldest open row
  first** (sorted by `due_date`), full-then-partial, with a 1-cent tolerance
  (`_TOLERANCE = 0.01`, line 686) to avoid stranding a plan in `Частично` forever from rounding.
  When every row reaches `Оплачен`, `status → Завершен`. This is the *only* payment-allocation
  entry point; there is no separate "pay off early" API — an early payoff is just a large
  `apply_payment` call that happens to zero out every remaining row in one shot (see C6).

**Installment Schedule row status** (`installment_schedule.json`): `Ожидает` (pending) →
`Частично` (partial) / `Просрочен` (overdue, set by the daily job) → `Оплачен` (paid).

### A3. «Закрытая / завершённая рассрочка» (fully-paid / closed plan)

There is no separate "close" action. A plan becomes `status=Завершен` purely as a side effect of
`apply_payment` once every schedule row (including row 0) is `Оплачен` (`installment_plan.py:731-732`).
It remains `docstatus=1` forever (submittable doctype, `allow_amend=0` — Frappe makes it read-only
natively). `Завершен` is included in `_LIVE_PLAN_STATUSES = ("Активный","Просрочен","Завершен")`
used everywhere in `profit.py` and the reports, so a completed plan's historical margin/interest
keep contributing exactly like an active one — correctly, since the sale genuinely happened.

### A4. «Выкуп / досрочное погашение» (early buyout / early payoff)

**No explicit mechanism exists.** Grepped the whole app for buyout/payoff/"досрочн" — zero hits
outside test names and Trade-In's unrelated "Выкуп товара" (buyout of a traded-in *device*, not a
loan). Early payoff = the customer's next `Payment Transaction` happens to be large enough that
`apply_payment` walks through and closes every remaining schedule row in one call. There is no
interest waiver/rebate logic — the customer pays 100% of the originally-scheduled `total_interest`
regardless of how early they pay. See C6 for how the three profit methods each handle this (by
design, not a bug, but worth verifying explicitly).

### A5. «Возврат / отказ» (return / refund / rejection)

**No explicit refund/return doctype or workflow exists.** Concretely:
- `Payment Transaction.amount` can never be negative or zero through the normal document flow:
  `_apply_table_payment_totals` (`payment_transaction.py:570-621`) **skips** any `payment_lines`
  row with `amount <= 0` (line 580) and **throws** `"Сумма по строкам оплаты должна быть больше
  нуля"` if the total is ≤ 0 (line 597-598). So a "negative Payment Transaction as a refund" is
  not creatable via the UI/API — only via direct DB manipulation that bypasses `validate()`.
- The only two working reversal paths are:
  1. **Cancel the whole Installment Plan** (`installment_plan.py: before_cancel/on_cancel`) —
     cascades PT cancellation, releases credit limit, flips stock back to available. All-or-
     nothing: you cannot reverse just *one* payment or *part* of a deal without cancelling
     everything. Also: the cashbox entries for the reversed payments are **deleted outright**
     (`_remove_payment_from_cashbox`, `payment_transaction.py:455-482`), not recorded as an
     offsetting `Расход` (cash-out) row — so there is no audit trail in the Cashbox of "money was
     paid back out" on the day of the return; the cashbox's historical income for that day is
     silently rewritten.
  2. **Cancel the cash Sales Order** — but this is broken per C2 (doesn't reverse the PT at all).
- **Trade In** (`trade_in.json`, `trade_in.py`) is the closest thing to a "return" doctype but it
  models the opposite direction — the shop *acquiring* a device from the customer (as a payment
  or standalone), not a customer returning a purchased device. See C7.

### A6. Payment Transaction

`payment_transaction.json`. `status` options (line 79-86): `Ожидает` (pending, docstatus=0) →
`Завершен` (posted, docstatus=1) → `Отменен` (cancelled, docstatus=2). No "type" field beyond
`payment_method` (cash/card/etc, a `Select` of channel names) — there is no
refund/reversal/adjustment transaction *type*, only cancellation of the whole document.
- `on_submit` (line 516-535): `status→Завершен`, `allocate_payment_transaction_to_installment_plan`
  (only fires when `reference_doctype == "Installment Plan"`; Sales-Order-referenced PTs skip
  allocation since the SO is already fully paid by definition), `_sync_payment_to_cashbox`.
- `on_cancel` (line 537-548): `status→Отменен`, `_deallocate_payment_from_installment_plan`
  (zeroes the specific schedule rows this PT paid, reopens `Завершен`→`Активный`/`Черновик` plan
  status if needed), `_remove_payment_from_cashbox` (deletes, doesn't offset, the cashbox rows).

### A7. Trade-In, Collection Log, Cash Handover, Cashbox / Cashbox Transaction

- **Trade In** (`trade_in.py`): `status` = `Черновик` → `Принят` (accepted, on submit) →
  `Отменен`. On submit, creates a real submitted `Stock Entry` (Поступление) at
  `rate = appraisal_amount` — this becomes the future COGS if that device is later resold
  (correctly feeds `_cogs_for_imei`). `payout_method` = `Наличные` (cash out of the branch's
  master cashbox, `_record_cashbox_payout`) / `В счёт покупки` (credit toward a **draft cash
  Sales Order only** — `linked_sales_order`, no equivalent field for Installment Plan) /
  `Удержано` (withheld — no side effect at all). See C7 for the payment-lines gap this creates.
- **Collection Log** (`collection_log.py`): pure activity log (who logged a collection attempt,
  no monetary side effects) — doesn't feed `profit.py` or any of the 4 reports.
- **Cash Handover** (`cash_handover.py`) / **Cashbox** / **Cashbox Transaction**: internal
  cash-custody transfer chain (salesperson cashbox → branch master cashbox), independent of
  `Payment Transaction`'s own bookkeeping. `profit.py` never reads Cashbox data — it reads
  `Payment Transaction` directly — so cashbox misstatements (e.g. the "deleted not offset" issue
  in A5, or the Trade-in cash-labeling issue in C7) don't directly corrupt P&L, but do corrupt the
  Cashbox's own balance history and any future "Cashbox Income by Period" report.

---

## B. How each report computes

### B1. `nasiya365/api/profit.py` — the shared profit engine

`compute_profit(from_date, to_date, branch)` (line 227-241) dispatches on
`Merchant Settings.profit_method` (`По оплате (касса)` default / `При продаже (начисление)` /
`Возмещение затрат`), then folds in `_period_expenses` and `_apply_basis` (`profit_basis`:
`Только маржа` / `Валовая прибыль` / `Чистая прибыль`, controls whether interest/expenses are
included in `gross_profit`/`net_profit`). This same `compute_profit` powers the P&L Page and
**Shareholder Distribution** (`shareholder_distribution.py:16`: `net = compute_profit(...)
["net_profit"]`, then split via `compute_shareholder_split`).

- **`_compute_cash`** (line 299-381, default method): iterates every completed Payment
  Transaction (`docstatus<2 AND status='Завершен'`) in `[from,to]`. For each, looks up its deal
  (`Installment Plan` or `Sales Order`), computes the deal's embedded margin/interest/denominator
  once (`_plan_profit`/`_so_profit`), and recognizes `amount/denom` of it. **Skips `amount <= 0`**
  (line 333) — no refund handling. **Cancellation guard is `plan.contract_status == "Отменен"`**
  (line 347) — dead per A2/C3; the *actual* protection against cancelled plans is that their PTs
  get cascade-cancelled to `docstatus=2` and thus never appear in the query in the first place.
  For `Sales Order` refs, **no `so.docstatus` check at all** (line 357-369) — see C2.
  **Branch** resolved via `_PAYMENT_BRANCH_CASE` (line 40-47): for Installment-Plan PTs, only via
  `ip.sales_order → so.branch` — see C1.

- **`_compute_accrual`** (line 386-451): full margin+interest recognized at `start_date`
  regardless of collection. Plans filtered `ip.docstatus=1 AND status IN (_LIVE_PLAN_STATUSES) AND
  contract_status != 'Отменен'` (line 398-400) — correctly excludes cancelled plans via
  `docstatus=1`, the `contract_status` clause is redundant/dead. Cash sales filtered
  `so.docstatus=1 AND NOT EXISTS (linked non-cancelled Installment Plan)` (line 423-428) —
  **correctly excludes cancelled cash sales** (unlike `_compute_cash`). Branch resolved via
  `_branch_clause_for("ip")` = same `ip.sales_order` join — see C1.

- **`_compute_cost_recovery`** (line 470-573): Раздел 1 = reuses `_compute_accrual` verbatim
  (labelled "potential", explicitly allowed to disagree with Раздел 2 per the design doc).
  Раздел 2 = for every deal with ≥1 completed payment in-window, computes
  `recognized_delta(collected_before, collected_after, cogs, total_profit)` via
  `nasiya365/api/recognition.py` (pure functions, unit-tested in
  `nasiya365/tests/test_cost_recovery_engine.py`): profit = `min(collected - cogs, total_profit)`,
  capped, monotonic, mathematically can't double-count *within this formula*. Comment at
  line 481-483 claims negative payments (refunds) "flow through" — true of the arithmetic, false
  in practice since such a PT can never exist (A5). Same dead `contract_status` guard (line 521),
  same `ip.sales_order`-only branch resolution (via `_PAYMENT_BRANCH_CASE` reused from
  `_compute_cash`), same missing `so.docstatus` check for Sales-Order deals in the window query.

- **COGS matching** — `_cogs_for_sale_item` (line 141-150): prefers the sale's own `stock_entry`
  link (`_cogs_from_stock_ref`, exact lot, unambiguous) and only falls back to
  `_cogs_for_imei` (line 58-103, date-aware nearest-purchase-on-or-before-sale-date, full-IMEI
  match preferred over fuzzy last-6-digits) when no explicit ref is set. Designed exactly for the
  "same IMEI bought twice" case (comment at line 62-64) and looks structurally sound for that case
  — risk is narrower than "any resold device," see C8.

### B2. `nasiya365/nasiya365/report/shareholder_distribution/shareholder_distribution.py`

Thin wrapper: `compute_profit(from,to,branch)["net_profit"]` → `compute_shareholder_split`.
100% inherits every profit-engine issue above (C1-C8 all propagate here 1:1) plus its own: no
extra filtering logic of its own to audit.

### B3. `nasiya365/nasiya365/report/sales_report/sales_report.py`

Cash section (line 42-90): `Sales Order` filtered **`so.docstatus=1`** (correctly excludes
cancelled/returned cash sales — the one report that gets this right) `AND NOT EXISTS (linked
non-cancelled Installment Plan)`, in `[from,to]` by `order_date`, per-item COGS via
`_cogs_for_sales_order`. Financed section (line 93-135): `Installment Plan` filtered
`docstatus=1 AND status IN (_LIVE_PLAN_STATUSES) AND contract_status != 'Отменен'`, by
`start_date`. Same `ip.sales_order`-only branch resolution (`_branch_clause_for`) — C1 applies.
Revenue here = `principal_amount` (full contract price) at time of **sale**, not collection — this
report is accrual-flavoured by construction, independent of `Merchant Settings.profit_method`.

### B4. `nasiya365/nasiya365/report/collections_and_overdue/collections_and_overdue.py`

Per-plan snapshot: `total_amount`, `paid_amount`, `remaining_balance` read straight off the
`Installment Plan` header (denormalized fields maintained by `apply_payment`/`validate` — see C9
for a rounding-drift note), `overdue_amount`/`days_overdue` computed live from schedule rows with
`due_date < today AND status IN (Ожидает,Частично,Pending)`, `collected_in_period` = sum of
completed PTs on that plan in `[from,to]`. Plans filtered the same way as B3 (`docstatus=1`,
`_LIVE_PLAN_STATUSES`, dead `contract_status` check, `ip.sales_order`-only branch — C1 applies).

### B5. `nasiya365/nasiya365/report/suppliers_payable/suppliers_payable.py`

Entirely independent of `profit.py` / Payment Transaction / Installment Plan. Reads `Stock Entry`
(`entry_type='Поступление'`, `docstatus=1`, has a `supplier`) joined to `Warehouse.branch` (a
*real* direct branch field — this report's branch clause, `_stock_entry_branch_clause`, is
correctly implemented and NOT subject to C1). `balance_due`/`paid_amount` are maintained
externally by Supplier Payment allocation (not read during this investigation — out of the given
scope, flagging only that this report is structurally decoupled from the BNPL deal lifecycle
questions in section A and is the *lowest-risk* of the five in scope).

### B6 (bonus, same engine). `profit_and_loss_summary.py`

Same `compute_profit` call as the P&L Page; renders either the legacy 11-line view or the
Раздел-1/Раздел-2 cost-recovery view depending on `profit_method`. No independent logic to audit
beyond B1.

### Access gating (relevant to how the branch bug actually manifests)

Checked every report's `.json`: `sales_report`, `collections_and_overdue`,
`shareholder_distribution`, `profit_and_loss_summary` (and the P&L Page) are all
`roles: [System Manager, Nasiya365 Admin]` only — the two `_UNRESTRICTED_ROLES`
(`nasiya365/permissions.py:5`). `suppliers_payable` additionally allows `Branch Manager` /
`Warehouse Manager`, but per B5 that report doesn't touch the Installment-Plan branch path at all.
**Practical consequence:** the branch-resolution bug (C1) is not a "branch-scoped role sees zero
data" story (only admins can open these reports) — it's a **"the branch filter dropdown silently
zeroes out installment data even for an admin"** story, which is arguably worse because it will
look like a genuine data/calculation bug to the very people trusted to audit the numbers.

---

## C. Discrepancy hypotheses

Ordered roughly by expected real-world impact.

### C1. [CRITICAL] Branch filter drops all new-style installment-plan revenue

**Scenario:** Any of the four report's `branch` filter is set to a specific branch (this is a
first-class filter field on every report in scope), and the deals in question are Installment
Plans created through the current, normal desk workflow (device picked via `stock_entry`, no
`sales_order`).

**What the code does:** Every branch clause for Installment-Plan data
(`nasiya365/api/bnpl_dashboard.py:19-39 _user_branch_clause`, reused as `profit.py:172-174
_branch_clause_for`, and inlined again as `_PAYMENT_BRANCH_CASE` at `profit.py:40-47`) resolves
branch **exclusively** via `ip.sales_order → Sales Order.branch`. But
`installment_plan.json:139-146` shows `sales_order` is `"hidden": 1`, labelled *"Заказ на продажу
(импорт / legacy)"* — legacy/import only. Confirmed via `installment_plan.js` (0 references to
`sales_order`) and `data_import.py:449-461` (only the *import* path ever populates it) that the
current plan-creation flow never sets this field.

**Why it's wrong:** For any such plan, the branch-resolution subquery returns `NULL`. Then:
- `_compute_cash` (`profit.py:326-330`): `if branch and b != branch: continue` — `None != branch`
  is always true, so the payment is dropped whenever a specific branch is selected.
- `_compute_accrual` (`profit.py:388-403`) / Sales Report / Collections & Overdue: the `EXISTS
  (SELECT ... branch=%s)` join/subquery on `ip.sales_order` matches nothing → the plan row itself
  is excluded from the SQL result set.
- Net effect: filtering P&L, Shareholder Distribution, Sales Report, or Collections & Overdue by
  **any specific branch** silently omits every recent/normal installment deal's revenue, margin,
  interest, and collections for that branch — while the same reports with **no branch filter**
  ("all branches") show the correct total. This is a strong, precise match for "reports show and
  calculate incorrectly" if branch-level reporting is how the merchant actually reviews the
  numbers (very likely for a multi-branch BNPL operator).
- Cash sales (`Sales Order.branch` is a required direct field) are **not** affected — so a branch
  filter would show plausible-looking (but wrong, financed-business-shaped-hole) cash-only
  numbers, making the bug easy to miss on a first glance and easy to misdiagnose as "our
  installment business isn't profitable at branch X" rather than a reporting defect.

### C2. [HIGH] Cash-sale cancellation/return doesn't reverse its Payment Transaction

**Scenario:** A customer returns a device bought via a cash `Sales Order`; staff cancel the
Sales Order (the only return mechanism that exists for cash sales, per A5).

**What the code does:** `SalesOrder.on_cancel` (`sales_order.py:45-48`) only sets
`status="Отменен"` and calls `reverse_stock()`. It never looks up or cancels the
`Payment Transaction` that `create_cash_receipt()` (`sales_order.py:230-285`) auto-created and
submitted at the time of sale.

**Why it's wrong:** That Payment Transaction remains `docstatus<2, status='Завершен'` forever.
- `_compute_cash` / `_compute_cost_recovery` (`profit.py`, Sales-Order branch of the loop,
  line 357-369 and 533-547): looks up the SO by `pay.rn`/`w.rn` via `frappe.db.get_value(...)`
  with **no `docstatus` filter at all** — happily keeps recognizing the (now-returned) sale's
  margin every time the report is run for that period.
- Meanwhile `sales_report.py:56` **does** filter `so.docstatus = 1`, so the Sales Report correctly
  drops the returned sale. **Result: P&L/Shareholder Distribution overstate profit for a returned
  cash sale that the Sales Report correctly excludes — same underlying event, two different
  numbers**, which is about as sharp a "the reports don't agree / are wrong" symptom as it gets.
- Also note stock *is* correctly reversed (`reverse_stock()`), so the item is resellable, but the
  first sale's phantom revenue/margin never goes away.

### C3. [MEDIUM] `contract_status` cancellation guard is dead code (works today by coincidence)

**Scenario:** Any code path that cancels/writes off a plan **without** going through
`InstallmentPlan.cancel()` (e.g. a future data-repair script, a bulk SQL update, an import
correction) — or simply: reading the code to understand what protects reports from cancelled
plans.

**What the code does:** `_compute_cash` (`profit.py:347`), `_compute_cost_recovery` (`profit.py:521`),
`_compute_accrual`'s SQL (`profit.py:400`), `sales_report.py:109`, `collections_and_overdue.py:72`,
and `payment_transaction.py:696` all guard with `contract_status != 'Отменен'` (or the DB
equivalent). Grepped exhaustively: **nothing in the codebase ever writes `contract_status =
'Отменен'`.** The Select option exists but is unreachable.

**Why it's wrong (today, latent):** Right now this doesn't cause visibly wrong numbers because
the *actual* protection is different and (mostly) works: `_compute_accrual`/Sales
Report/Collections use `ip.docstatus=1` (a cancelled plan is `docstatus=2`, correctly excluded);
`_compute_cash`/`_compute_cost_recovery` rely on the fact that `InstallmentPlan.before_cancel`
cascades `.cancel()` onto every linked Payment Transaction, so those PTs disappear from the
`docstatus<2` window on their own. **But this is fragile**: it only holds because cancellation
*always* goes through the one blessed code path. Any future direct `docstatus`/status write on
Installment Plan (bulk correction, patch script, a bug elsewhere) that doesn't also cascade-cancel
the PTs would leave the PT-derived cash/cost-recovery numbers wrong, and nothing in `profit.py`
would catch it — the guard that's supposed to catch exactly this (`contract_status`) never fires.
Also: the unit test `test_cost_recovery_engine.py:62` seeds `contract_status="Активный"`, which
isn't even a valid Select option (`Не подписан|Подписан|Отменен`) — a sign the field's contract is
already confused/untested for the cancellation case it's named for.

### C4. [MEDIUM-HIGH] Refunds are unrepresentable; cash-basis silently ignores the one path that theoretically supports them

**Scenario:** Customer is refunded part of what they paid (device defect, downgrade, goodwill),
without the deal being fully cancelled.

**What the code does:** Per A5, a negative/refund Payment Transaction cannot be created through
normal validation. `_compute_cash` additionally does `if amount <= 0: continue`
(`profit.py:333`) — even in the hypothetical world where such a row existed, the **default profit
method** would just skip it rather than reversing prior recognition. Only
`_compute_cost_recovery`'s comment (`profit.py:481-483`) claims to handle it, and that code path
is unreachable per A5.

**Why it's wrong:** There is no way to record "we gave $50 back to this customer without undoing
the whole sale" that any report reflects. The merchant's only lever is the all-or-nothing plan
cancellation (C3's cascade), which over/under-reverses relative to an actual partial refund.

### C5. [MEDIUM] "Списан" (written-off) plans keep recognizing new/old payments in cash-basis and cost-recovery

**Scenario:** A plan is manually marked `status=Списан` (bad debt write-off) after partial
collection.

**What the code does:** `_LIVE_PLAN_STATUSES = ("Активный","Просрочен","Завершен")` (used by
`_compute_accrual`, Sales Report, Collections & Overdue) **excludes** `Списан` — so a written-off
plan disappears from the "sales made" (Раздел 1) and Collections & Overdue view entirely, even for
its *historical*, already-collected payments. But `_compute_cash`/`_compute_cost_recovery` never
check `plan.status` at all (only fetch it, unused) — so if that written-off plan's Payment
Transactions still sit in `[from,to]` (already collected before write-off, or hypothetically
collected after), their profit is still recognized in the cash/cost-recovery numbers.

**Why it's worth verifying, not just asserting a bug:** Money genuinely collected before write-off
*is* real profit — recognizing it isn't obviously wrong. But the **inconsistency between reports**
(Collections & Overdue shows nothing for this plan; P&L cash-basis still shows its historic
margin) is exactly the kind of "the reports don't match each other" symptom worth walking the
merchant through with a concrete seeded example (see D — case 8).

### C6. Early buyout / lump-sum payoff — verify, likely correct but easy to mis-read as a spike

**Scenario:** Customer pays off the full `remaining_balance` in one large `Payment Transaction`
well before the schedule's final due date.

**What the code does:**
- **Accrual**: no effect — the deal's full margin+interest was already recognized at `start_date`
  regardless of collection speed.
- **Cash basis**: recognizes `amount/denom` of embedded margin+interest for *this* payment like
  any other — a big final payment recognizes a big final chunk. Sums to exactly 100% over the
  deal's life (by construction, `Σamount == total_amount == denom`) — no double count, no leakage.
- **Cost recovery**: `recognized_delta` jumps from wherever `collected_before` was straight to
  `min(collected_after - cogs, total_profit)` — if the lump sum crosses (or is already past) the
  COGS threshold, **100% of the remaining margin AND 100% of `total_interest`** get recognized in
  that single period, even though, temporally, the loan hadn't "earned" all its interest yet under
  a time-based accrual notion. This is the documented, intentional design (cost-recovery design
  doc §3) — not a bug — but it will look like an anomalous one-period profit spike to whoever
  reads the P&L, and should be part of the test matrix so it's understood, not mistaken for an
  error.

### C7. Trade-in as part-payment on a cash sale — cashbox/PT mismatch (two distinct failure shapes)

**Scenario:** Customer part-pays a cash `Sales Order` with a traded-in device
(`Trade In.payout_method = "В счёт покупки"`, `linked_sales_order` set to the draft SO).

**What the code does:** `TradeIn._apply_credit_to_sales_order` (`trade_in.py:187-202`) bumps
`Sales Order.paid_amount` directly by `appraisal_amount` — it never appends anything to
`Sales Order.payment_lines` (the split-payment child table). Two sub-cases at SO submit
(`sales_order.py:230-285 create_cash_receipt`):
1. **Customer also paid real cash via split `payment_lines`:** `split_lines` in
   `create_cash_receipt` only sees the genuine cash rows (trade-in never added a row) → the
   auto-created Payment Transaction's `amount` (sum of its lines) is **less than
   `so.total_amount`** by exactly the trade-in value. `profit.py`'s `_so_profit`/`frac =
   amount/denom` then permanently recognizes less than 100% of the deal's margin — the trade-in
   funded portion of the sale's profit **never gets recognized**, ever (no future PT will arrive
   for an already-fully-paid, submitted SO).
2. **No split lines at all (single-method legacy path):** falls to
   `receipt.append("payment_lines", {..., "amount": flt(self.paid_amount)})` — this uses
   `self.paid_amount`, which **does** include the trade-in bump, but labels the **entire** amount
   as `"Наличные USD"` (cash). `_sync_payment_to_cashbox` then posts a real cash-income row into
   the branch's cashbox for money that was never physically received — inflating recorded cash on
   hand and breaking the cashier's physical reconciliation.

**Why it's wrong:** Depending purely on whether the cashier happens to use split payment lines or
not, trade-in-funded cash sales either under-recognize P&L profit forever, or overstate cashbox
cash. Neither is correct; there's no code path that gets both the cashbox and the P&L right
simultaneously for this scenario.

**Separately:** Trade In has **no `linked_installment_plan` field at all** (`trade_in.json` field
list has only `linked_sales_order`) — a trade-in cannot be formally linked as a down payment on an
*installment* sale. If a merchant does this today, it must be two disconnected documents (a
`Наличные`/`Удержано` Trade In, plus a manually-reduced `down_payment` typed into the Installment
Plan) with no data linkage — worth confirming with the merchant whether this is actually attempted
in practice, since the "expected" report impact for that case is undefined by the code as it
stands (see D — case 10 treats it as the realistic workaround, not a supported flow).

### C8. COGS-by-IMEI lot matching — narrower risk than "any resale corrupts COGS"

**Scenario:** Same physical IMEI purchased into stock more than once (e.g. bought → sold →
traded back in as a used unit at a different cost → resold).

**What the code does:** `_cogs_for_sale_item` (`profit.py:141-150`) prefers an explicit
`stock_entry`/Stock-Entry-Item reference (unambiguous — always right when set). Only when that's
absent does it fall back to `_cogs_for_imei` (`profit.py:58-103`), which is explicitly date-aware:
picks the purchase lot with the latest `posting_date <= as_of_date` (the sale's own date),
preferring an exact full-IMEI match over a fuzzy last-6-digits match. This looks like it was
built specifically to survive the "bought twice" case.

**Residual risks worth testing rather than assuming safe:**
- The fuzzy **last-6-digits** fallback (`profit.py:83-91`) could theoretically cross-match two
  genuinely different devices that happen to share their last 6 IMEI digits — low probability but
  nonzero at scale, and silent (no error, just a wrong COGS).
- Multi-item cash Sales Orders (`_cogs_for_sales_order`, `profit.py:153-169`) only use the
  explicit `stock_entry` shortcut `when len(items) == 1`; a multi-line SO always falls through to
  the date-aware IMEI lookup per line — more surface area for the fallback path to matter.
- `as_of_date` for a plan is `plan.start_date`; if the *trade-in re-intake* happens to be posted
  with a `posting_date` **before or equal to** the original sale's `start_date` (e.g. bad manual
  data entry, or a same-day back-and-forth), the "on/before" ordering assumption inverts and the
  wrong lot could be picked. Worth an explicit seeded case (D — case 11) rather than reasoning
  about it in the abstract.

### C9. Minor: rounding-drift between header fields and live schedule sums

`Installment Plan.paid_amount`/`remaining_balance` are denormalized fields written by
`apply_payment`/`validate` (`installment_plan.py:712-724`), with an explicit "warn if row sum
drifts from total_amount by > 1 cent" check (`_validate_schedule_sum`, line 602-617) that only
*warns*, never *fixes*, on save. `collections_and_overdue.py` reads these header fields directly
(not recomputing from schedule) for `total_amount`/`paid_amount`/`remaining_balance`, but computes
`overdue_amount` live from the schedule. On an old/edited plan where the header has drifted from
the schedule (structural edit after payments — `_warn_structural_change_on_paid_plan`,
`installment_plan.py:457-473`, only warns, doesn't block), Collections & Overdue's "Остаток" column
and its live-computed "Просрочено" column could tell slightly inconsistent stories. Low severity,
but cheap to include one seeded case for (D — case 9).

---

## D. Test-case catalogue

All scenarios assume a fresh test IMEI/customer/branch per case to avoid cross-contamination
(follow the pattern in `nasiya365/tests/test_cost_recovery_engine.py` — `_db_insert` helpers,
per-test SAVEPOINT/ROLLBACK, a "safe" calendar year clear of real data). For each case: records to
create, then expected impact per report (P&L under **all three** `profit_method`s where it
differs, Shareholder Distribution = derived from P&L's `net_profit`, Sales Report, Collections &
Overdue).

1. **Cash sale, plain.** One `Sales Order` (docstatus=1, fully paid, single payment method,
   `branch` set). *Expect:* Sales Report shows it once (cash section); P&L cash/accrual/cost-
   recovery all recognize `total_amount - COGS` as margin, no interest, on `order_date`;
   Collections & Overdue unaffected (cash sales aren't installment plans).

2. **Installment sale, new-style, down payment only (no regular payments made).** One
   `Installment Plan` created via `stock_entry` (no `sales_order`), submitted, with `down_payment
   > 0` and the down-payment schedule row (`installment_number=0`) still unpaid. *Expect:* Sales
   Report / P&L accrual (Раздел 1) recognize the full potential margin+interest at `start_date`
   regardless of payment status (accrual is payment-agnostic); P&L cash-basis / cost-recovery
   Раздел 2 recognize **$0** (nothing collected yet); Collections & Overdue shows full
   `remaining_balance`, `overdue_amount=0` if within grace, `collected_in_period=0`. **Also**:
   filter every report by this plan's branch specifically — confirm C1 (installment data should
   vanish under branch filter, present under "all branches").

3. **Installment sale with several installments partially paid, still active.** Same as #2 but
   with 2-3 of N installments paid via separate `Payment Transaction`s across different dates.
   *Expect:* cash-basis recognizes each payment's proportional margin/interest independently;
   cost-recovery recognizes $0 until cumulative collected crosses COGS, then the crossing-period
   recognizes the full remainder up to `total_profit`; Collections & Overdue's `paid_amount`
   matches the header, `collected_in_period` matches the sum of in-window PTs.

4. **Completed installment plan (fully paid on schedule).** All schedule rows `Оплачен`,
   `status=Завершен`. *Expect:* summed across the deal's entire life, cash-basis Σrecognized ==
   margin+interest (within a cent, per C9); cost-recovery final recognized == margin+interest
   exactly; Collections & Overdue shows `remaining_balance=0`, excluded once `only_overdue=1` is
   set (no overdue rows), still appears in the base list (status is in `_LIVE_PLAN_STATUSES`).

5. **Overdue installment plan.** Plan with ≥1 schedule row `due_date` in the past and
   `status=Просрочен` (either run the daily job or set status directly for the test). *Expect:*
   Collections & Overdue's `overdue_amount`/`days_overdue` populated correctly; P&L (all methods)
   treats it identically to an active plan — overdue-ness has zero effect on recognized numbers,
   confirm this explicitly.

6. **Cancelled installment plan — before any payment.** Draft or submitted plan with zero PTs,
   then `.cancel()`. *Expect:* excluded everywhere (docstatus=2); no visible effect on any report
   for the period, including the period of the original `start_date`.

7. **Cancelled installment plan — after partial payment.** Submitted plan, 1-2 payments made
   (spanning two report periods, to test C3's retroactive-recompute characteristic), then
   `.cancel()`. *Expect:* cascade should cancel the PTs (`docstatus→2`); re-running the report for
   the **earlier** period (when the now-cancelled payment was originally collected) should show
   **zero** for that deal too, even though at the time it was run it would have shown the
   collected amount — explicitly confirm this "numbers change retroactively when something is
   cancelled later" behavior, since it's a very plausible source of "the report used to say X, now
   it says Y" complaints. Also confirm the plan's `Contract` doc got cancelled/deleted and stock
   `business_status` reverted to available.

8. **Written-off plan («Списан») after partial collection.** Submitted plan, 1 payment collected,
   then `status` manually set to `Списан`. *Expect:* per C5 — Sales Report / Collections & Overdue
   / P&L accrual (Раздел 1) drop it entirely; P&L cash-basis / cost-recovery Раздел 2 **still**
   show the historical collected payment's margin for the period it was actually collected in.
   Document the resulting cross-report inconsistency explicitly as expected-per-current-code (not
   a fix target here).

9. **Early buyout / lump-sum payoff.** Submitted plan, single large `Payment Transaction` for the
   full `remaining_balance` collected well before the final scheduled due date, crossing the
   cost-recovery COGS threshold in one step. *Expect:* per C6 — accrual unaffected; cash-basis
   recognizes proportionally in the payoff period; cost-recovery recognizes the *entire* remaining
   margin+interest in the payoff period (a visible spike) — confirm this matches
   `recognized_delta` exactly (unit-test-style assertion, not just eyeballing the report).

10. **Return/refund of a cash sale (before and after cancellation attempt).** Fully paid cash
    `Sales Order`, then cancel it. *Expect:* per C2 — Sales Report drops it (correct); P&L (any
    method) **still shows its margin** (the bug) — this is the single clearest repro to hand to
    the merchant. Run "before cancel" and "after cancel" report snapshots side by side.

11. **Return/refund of an installment plan (before and after payments).** Two sub-cases: (a)
    cancel a plan with zero payments — clean, matches case 6; (b) cancel a plan with 1-2 payments
    already collected and *already reported* in a prior period — matches case 7's retroactive
    effect. Use this pair specifically to contrast with case 10: installment cancellation *does*
    reverse the PT (correctly, if harshly/retroactively); cash-sale cancellation does not at all.

12. **Trade-in as part-payment on a cash sale, split-payment path.** Draft SO + cash payment_line
    (partial) + Trade In (`payout_method=В счёт покупки`, `linked_sales_order`), submit both.
    *Expect:* per C7 sub-case 1 — the resulting Payment Transaction's `amount` is short by the
    trade-in value; P&L margin for this sale is permanently under-recognized versus
    `so.total_amount - cogs`.

13. **Trade-in as part-payment on a cash sale, single-method path.** Same as #12 but with no
    `payment_lines` rows (legacy single-`payment_method` flow, relying on `so.paid_amount`).
    *Expect:* per C7 sub-case 2 — the auto-created PT is fully correct for P&L purposes (amount ==
    total_amount), but the Cashbox is overstated by the trade-in's appraisal value (cash income
    posted for money never physically received) — check via `Cashbox Transaction`, not one of the
    4 reports in scope, but valuable corroborating evidence.

14. **Device sold, returned, and re-sold (same IMEI, two purchase lots).** Seed: purchase A
    (Stock Entry, cost $X) → sell via Installment Plan #1 (cancel it, per case 7/11b) → Trade-In
    re-intake of the *same* IMEI (Stock Entry, cost $Y ≠ $X, later `posting_date`) → sell via
    Installment Plan #2. *Expect:* Plan #1's (reversed) COGS and Plan #2's COGS should each match
    their respective purchase lot ($X then $Y) via `_cogs_for_imei`'s date-aware matching (C8) —
    assert both explicitly, and assert Plan #2's COGS is **not** accidentally still $X.

15. **An operating expense.** One `Expense` (docstatus=1, `status=Оплачен`) inside the period, one
    outside, one cancelled (`status=Отменен`, docstatus=2). *Expect:* only the in-period,
    non-cancelled expense reduces `net_profit` (`profit_basis = Чистая прибыль` only —
    confirm `Только маржа`/`Валовая прибыль` correctly exclude it per `_apply_basis`).

16. **Branch filter with mixed legacy + new-style plans.** One legacy-imported plan (`sales_order`
    set, via the `data_import.py` path or by seeding the field directly) and one new-style plan
    (no `sales_order`), same branch, same period. *Expect:* filtering by that branch shows **only**
    the legacy plan's numbers; "all branches" shows both — the clearest possible repro of C1,
    directly contrasting the two plan "eras."

17. **Period with zero data.** Empty date range / branch with nothing in it. *Expect:* every
    report returns zero rows / zero totals without error (sanity/regression baseline, also a good
    place to confirm `Merchant Settings` with no shareholders configured triggers the
    `frappe.msgprint` in `shareholder_distribution.py:32` rather than crashing).

18. **Same deal, three `profit_method`s, one comparison run.** Take case 3's deal (partial
    payments, still active) and run `compute_profit` under all three `profit_method` settings for
    the *same* period. *Expect:* differing `financed_margin`/`interest_income`/`net_profit` by
    design (documented in the cost-recovery spec) — use this as the baseline "these numbers are
    supposed to differ" case so it isn't mistaken for a bug when building the rest of the matrix.

---

## Files referenced (for quick navigation)

- `nasiya365/api/profit.py` — profit engine (all three methods, COGS helpers, branch clauses)
- `nasiya365/api/recognition.py` — pure cost-recovery math
- `nasiya365/nasiya365/doctype/installment_plan/installment_plan.py` + `.json`
- `nasiya365/nasiya365/doctype/payment_transaction/payment_transaction.py` + `.json`
- `nasiya365/nasiya365/doctype/sales_order/sales_order.py` + `.json`
- `nasiya365/nasiya365/doctype/trade_in/trade_in.py` + `.json`
- `nasiya365/nasiya365/doctype/stock_entry/stock_entry.py`
- `nasiya365/nasiya365/doctype/expense/expense.py`
- `nasiya365/nasiya365/doctype/contract/contract.py`
- `nasiya365/nasiya365/doctype/cashbox/cashbox.py`, `cashbox_transaction.py`, `cash_handover.py`,
  `collection_log.py`
- `nasiya365/api/bnpl_dashboard.py` (`_user_branch_clause`, stock-status refresh helpers)
- `nasiya365/permissions.py` (`_UNRESTRICTED_ROLES`, `_is_unrestricted`, `_get_user_branches`)
- `nasiya365/nasiya365/report/{shareholder_distribution,sales_report,collections_and_overdue,
  suppliers_payable,profit_and_loss_summary}/*.py`
- `nasiya365/tasks/daily.py`, `nasiya365/data_import.py`
- `nasiya365/tests/test_cost_recovery_engine.py`,
  `nasiya365/nasiya365/doctype/installment_plan/test_installment_plan.py`,
  `nasiya365/nasiya365/doctype/payment_transaction/test_payment_transaction.py`
- Prior design docs: `docs/superpowers/specs/2026-07-28-pnl-cost-recovery-design.md`,
  `docs/superpowers/specs/2026-07-28-pnl-redesign-foundation.md`
