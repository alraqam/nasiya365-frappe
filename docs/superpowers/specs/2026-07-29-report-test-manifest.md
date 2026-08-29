# Report-correctness test manifest — seeded DEV scenarios

**Seeded:** 2026-07-29 on DEV (`my.nasiya365.uz`) via `db_insert` (the proven pattern from
`nasiya365/tests/test_cost_recovery_engine.py`). 18/18 scenarios committed, 0 failures.
**Isolation:** IMEIs `TST-<case>`, customers `Test C<case>`, test branches `BR-TEST-PNL-A` /
`BR-TEST-PNL-B`, window **2026-09-01 … 2026-10-31** (clear of real demo data ≤ 2026-07-25);
the overdue case starts 2026-05-05 (its past-dated schedule rows feed only the overdue calc,
never the profit SUMs). Re-running the seed is a safe no-op (aborts if `TST-` plans exist).

**No code was changed. Nothing was deleted. Nothing pushed/committed to git.** The report
matrix (`_report_matrix.py`) is fully read-only — it calls `_compute_cash/_accrual/
_cost_recovery` directly and folds profit-basis math locally, so it never mutates
`Merchant Settings`.

> **Note on method:** these are report-*observable states* seeded directly (the reports read
> stored columns + child tables, not controllers). For the flow-behavior bugs (C2 cancel-doesn't-
> reverse-PT, C7 trade-in), the seeded state is exactly what the real controller flow *leaves*
> per the code audit; a follow-up can re-seed those via real `.submit()/.cancel()`/trade-in
> flows to also exercise the controllers. The report-level discrepancy is identical either way.

## Coverage

| # | Scenario | Key records (imei / customer) | Test with (window · branch · method) | Fully seeded |
|---|----------|------------------------------|--------------------------------------|:---:|
| 1 | Cash sale, plain | SO+PT, `TST-01`, br A | 09-05 · all · any | ✅ |
| 2 | New-style installment, down-only, no payments | plan `TST-02` / Test C02 (no SO) | 09-06 · all vs A · accrual & cash | ✅ |
| 3 | Partial active (2 of 4 paid) | plan `TST-03` / Test C03 | 09-07..10-07 · all vs A · all 3 | ✅ |
| 4 | Completed (fully paid, Завершен) | plan `TST-04` / Test C04 | 09-08..10-20 · all · cash/cr | ✅ |
| 5 | Overdue (Просрочен) | plan `TST-05` / Test C05 (starts 05-05) | C&O as-of today · all | ✅ |
| 6 | Cancelled before payment | plan `TST-06` (docstatus 2) | 09-09 · all | ✅ |
| 7 | Cancelled after payment (PT cascade-cancelled) | plan `TST-07` + PT (both ds 2) | 09-10 · all | ✅ |
| 8 | Written-off (Списан) after collection | plan `TST-08` / Test C08 | 10-11 · all · cash/cr | ✅ |
| 9 | Early buyout / lump-sum payoff | plan `TST-09` / Test C09 | 09-12 · all · costrec | ✅ |
| 10 | Return of a cash sale (SO cancelled, PT live) | SO ds2 + PT ds1, `TST-10` | 09-13 · all · cash + Sales Report | ✅ |
| 11 | Installment return pair (a: 0 pay, b: 1 pay reversed) | `TST-11A`, `TST-11B` (ds 2) | 09-14 · all | ✅ |
| 12 | Trade-in split-payment (PT short) | SO+PT(500), `TST-12` | 09-15 · all · cash | ✅ |
| 13 | Trade-in single-method (PT correct, cashbox over) | SO+PT(800), `TST-13` | 09-16 · all · cash | ✅ (cashbox delta noted, not seeded) |
| 14 | Sold-returned-resold, two COGS lots | `TST-14` (lot 600 then 550) | cogs @09-02 vs @09-19 | ✅ |
| 15 | Operating expense (in / out / cancelled) | Expense ×3, br A | 09-01..10-31 · all · 3 bases | ✅ |
| 16 | Branch filter, legacy + new-style plans | `TST-16L` (SO→A) + `TST-16N` (no SO) | 09-22 · all vs A · cash + C&O | ✅ |
| 17 | Zero-data window | (none) | 2027-01 · all | ✅ |
| 18 | Same deal, 3 profit_methods | reuses `TST-03` | 09-01..10-31 · all · 3 methods | ✅ |

## How to reproduce the matrix

Read-only runner: `_report_matrix.run()` (temp module, container `nasiya365-frappe-backend-1`).
The persistent seeded rows remain on dev for manual UI inspection at
`/app/profit-and-loss`, the Sales Report, and Collections & Overdue — filter by branch
`BR-TEST-PNL-A` to see the C1 drop live.
