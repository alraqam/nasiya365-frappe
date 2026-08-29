# Report discrepancies — CONFIRMED with numbers (DEV test)

Empirical confirmation of the code audit (`2026-07-29-pnl-lifecycle-and-report-audit.md`)
against 18 seeded scenarios. Numbers are actual output of the report engines
(`_compute_cash/_accrual/_cost_recovery`, Sales Report, Collections & Overdue) run on DEV.
No fixes applied — this is the diagnosis; fixes are a separate, permission-gated step.

---

## 🔴 C1 — Branch filter silently drops ALL new-style installment revenue — CONFIRMED (the likely cause)

**Root cause:** `Installment Plan` has **no branch field of its own**. Every report resolves a
plan's branch *only* through `plan.sales_order → Sales Order.branch`
(`profit.py:42-43`, `collections_and_overdue.py:34,43`). A plan created without `sales_order`
set is therefore **branch-less** and vanishes from any branch-filtered view.

**Production reality (measured on dev):** **4 of 4 real Installment Plans have `sales_order`
empty.** The current create-flow does not set it. So **100% of real installment revenue
disappears** the moment a report is filtered by a specific branch — or viewed by a
branch-restricted (non-admin) user, since the same clause gates their access.

**Numbers (case 16 — one legacy plan w/ SO→A + one new-style plan, same branch A, 2026-09-22):**

| Report | All branches | Filtered to branch A |
|--------|-------------:|---------------------:|
| P&L cash — margin+interest | **576.47** | **288.24** (exactly half — new-style plan gone) |
| Collections & Overdue — plans listed | 8 test plans | **only `Test C16-legacy`** (7 vanished) |

Under a branch filter a merchant sees **1 of 8** plans and **half** the profit. This matches
"reports don't agree with reality."

---

## 🔴 C2 — Cancelled/returned cash sale stays in P&L but drops off the Sales Report — CONFIRMED

`SalesOrder.on_cancel` reverses stock but never cancels the auto-created Payment Transaction.
The P&L cash/cost-recovery engines have **no `docstatus` check on the Sales Order**, so a
returned sale keeps producing profit forever, while the Sales Report (which checks
`docstatus=1`) correctly drops it → the two reports disagree.

**Numbers (case 10 cancelled vs case 01 normal, identical 800 sale / 600 COGS):**

| | P&L cash margin | Sales Report rows |
|---|---:|---:|
| case 01 — normal sale (09-05) | 200 | **1** |
| case 10 — cancelled sale (09-13) | **200** | **0** |

The cancelled sale contributes 200 profit that the Sales Report says doesn't exist.

---

## 🟠 C5 — Written-off («Списан») plan keeps producing P&L profit after write-off — CONFIRMED

A plan marked `Списан` drops out of Sales Report, Collections & Overdue and P&L accrual
(none of them list it), **but** cash-basis and cost-recovery still recognize its historically
collected payments.

**Numbers (case 08, Списан, 10-11 payment of 350):**

| | cash | cost-recovery | Collections & Overdue |
|---|---:|---:|---:|
| case 08 recognized margin | 102.94 | 35.71 (collected 350) | **not listed** |

P&L books margin on a bad-debt write-off that every other report treats as gone.

---

## 🟡 C6 — Early buyout books the whole deal's profit in one period (cost-recovery) — CONFIRMED (by design)

**Case 09** — single 1020 payoff crossing the 600 COGS threshold: cost-recovery recognizes the
**entire 420** (margin 300 + interest 120) in the payoff period — a visible spike. This is
correct per the cost-recovery model, documented here so it isn't mistaken for a bug when the
merchant sees a one-month jump.

---

## 🟠 C7a — Trade-in as part-payment permanently under-recognizes the sale's margin — CONFIRMED

When a trade-in covers part of a cash sale via the split-payment path, the resulting Payment
Transaction `amount` is short by the trade-in's value, so cash-basis recognizes less margin
than the sale actually earned.

**Numbers (case 12 = 500 cash + 300 trade-in vs case 13 = full 800):**

| | collected in PT | cash margin |
|---|---:|---:|
| case 13 — full payment | 800 | 200 (correct) |
| case 12 — 300 via trade-in | 500 | **125** (short by 75) |

The 300 of value received as a device is never recognized as margin.

---

## ✅ C8 — COGS-by-IMEI lot matching works (date-aware) — CONFIRMED CORRECT

**Case 14** — same IMEI bought at 600 then re-taken at 550 later: `_cogs_for_imei` returns
**600** as of the first sale date and **550** as of the second. Date-aware matching picks the
right lot; the earlier "any resale corrupts COGS" worry does **not** reproduce here. Residual
risk is narrow (only when two lots share indistinguishable dates).

---

## ✅ Baselines behaving correctly (not bugs)

- **case 15 / profit basis:** the in-period expense (150) reduces `net_profit` **only** under
  «Чистая прибыль» (net 2731.32 = gross 2881.32 − 150); «Только маржа» and «Валовая прибыль»
  correctly exclude it. ✅
- **case 17 / zero window:** empty range returns all zeros, no error. ✅
- **case 18 / three methods, same deal:** cash net 2731 · accrual net 3370 · cost-recovery net
  1540 — differ **by design**, not a defect. ✅

---

## Priority for fixes (separate, permission-gated step — NOT started)

1. **C1** — highest impact, matches the complaint. Options: give `Installment Plan` its own
   `branch` field set at creation, and/or resolve branch without requiring `sales_order`; and
   backfill the 4 (prod: all) existing plans.
2. **C2** — cancel the Payment Transaction in `SalesOrder.on_cancel` (and/or add a `docstatus`
   guard in the P&L SO branch).
3. **C5** — decide whether `Списан` plans should keep or drop already-recognized cash margin,
   and make all reports agree.
4. **C7a** — recognize trade-in value as part of the sale's collected amount.
