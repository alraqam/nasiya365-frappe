# P&L Cost-Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a third P&L recognition method — «Возмещение затрат» (cost recovery, variant A, per deal) — where a deal's profit is $0 until collections cover its COGS, then all collected above cost (margin + interest, split proportionally) is recognized; plus a two-section report view.

**Architecture:** Pure recognition math lives in a new Frappe-free module `nasiya365/api/recognition.py` (fast, deterministic unit tests). The DB-facing `_compute_cost_recovery` in `profit.py` gathers per-deal collections and calls those pure helpers. Раздел 1 of the report (sales/potential) reuses the existing `_compute_accrual`; Раздел 2 (recognized) uses the new numbers. The report renders the two-section layout only when the method is cost recovery; the existing layout is untouched for the other methods.

**Tech Stack:** Frappe v16 (Python), MariaDB, existing report framework. Tests: stdlib `unittest` for the pure module; manual browser verification on prod for integration.

## Global Constraints

- All monetary amounts on prod are **USD**; no currency conversion is performed. Label stays «USD». (Copied from spec §1.)
- **Read-only**: the report and engine never write to the DB. No migrations.
- **Additive**: do NOT modify or remove `_compute_cash` or `_compute_accrual`; they must keep returning identical results. Cost recovery is a new branch selected by `profit_method`.
- Recognition is **per deal**: Installment Plan (financed) or Sales Order not tied to a plan (cash).
- Reuse existing helpers unchanged: `_cogs_for_sale_item`, `_plan_profit`, `_so_profit`, `_user_branches`, branch/permission resolution.
- Method label string (exact): `Возмещение затрат`.
- Net profit for cost recovery must be driven by **recognized** margin/interest (so `_apply_basis` and `profit_basis` keep working without change).

---

### Task 1: Pure recognition math + unit tests

**Files:**
- Create: `nasiya365/api/recognition.py`
- Test: `nasiya365/tests/test_recognition.py`

**Interfaces:**
- Produces:
  - `recognized_amount(collected: float, cogs: float, total_profit: float) -> float`
  - `recognized_delta(collected_before: float, collected_after: float, cogs: float, total_profit: float) -> float`
  - `split_recognized(recognized: float, margin: float, total_profit: float) -> tuple[float, float]` → `(margin_part, interest_part)`

- [ ] **Step 1: Write the failing test**

Create `nasiya365/tests/test_recognition.py`:

```python
import unittest

from nasiya365.api.recognition import (
    recognized_amount,
    recognized_delta,
    split_recognized,
)


class TestRecognizedAmount(unittest.TestCase):
    def test_below_cost_is_zero(self):
        # collected 350 < cogs 620 -> nothing recognized
        self.assertEqual(recognized_amount(350, 620, 120), 0.0)

    def test_exactly_cost_is_zero(self):
        self.assertEqual(recognized_amount(620, 620, 120), 0.0)

    def test_above_cost_under_total(self):
        # collected 700, cogs 620 -> 80 above cost, below total profit 120
        self.assertEqual(recognized_amount(700, 620, 120), 80.0)

    def test_full_collection_is_total_profit(self):
        # collected 740 (= principal 650 + interest 90), cogs 620 -> 120
        self.assertEqual(recognized_amount(740, 620, 120), 120.0)

    def test_overpayment_clamped_to_total(self):
        self.assertEqual(recognized_amount(800, 620, 120), 120.0)

    def test_zero_profit_deal(self):
        self.assertEqual(recognized_amount(700, 620, 0), 0.0)

    def test_loss_deal_recognizes_nothing(self):
        # total_profit negative (sold below cost) -> no positive profit recognized
        self.assertEqual(recognized_amount(700, 620, -10), 0.0)


class TestRecognizedDelta(unittest.TestCase):
    def test_first_payment_below_cost(self):
        # before 0 -> 0 ; after 350 -> 0 ; delta 0
        self.assertEqual(recognized_delta(0, 350, 620, 120), 0.0)

    def test_full_life_recognizes_everything(self):
        # before 350 -> 0 ; after 740 -> 120 ; delta 120
        self.assertEqual(recognized_delta(350, 740, 620, 120), 120.0)

    def test_window_crosses_cost_boundary(self):
        # before 650 -> 30 ; after 700 -> 80 ; delta 50
        self.assertEqual(recognized_delta(650, 700, 620, 120), 50.0)


class TestSplitRecognized(unittest.TestCase):
    def test_full_split(self):
        # total profit 120 = margin 30 + interest 90
        self.assertEqual(split_recognized(120, 30, 120), (30.0, 90.0))

    def test_partial_split_is_proportional(self):
        # recognized 80 of a 30/90 deal -> 20 margin / 60 interest
        self.assertEqual(split_recognized(80, 30, 120), (20.0, 60.0))

    def test_zero_total_profit(self):
        self.assertEqual(split_recognized(50, 0, 0), (0.0, 0.0))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run (from repo root): `python3 -m unittest nasiya365.tests.test_recognition -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'nasiya365.api.recognition'`

- [ ] **Step 3: Write minimal implementation**

Create `nasiya365/api/recognition.py`:

```python
"""Pure profit-recognition math for the cost-recovery P&L method.

No Frappe imports — these helpers are deterministic so they can be unit-tested
in isolation. See docs/superpowers/specs/2026-07-28-pnl-cost-recovery-design.md.

Cost recovery (variant A, per deal): a deal recognizes no profit until the money
collected on it covers its cost of goods (COGS); every dollar collected beyond
COGS is profit, capped at the deal's total profit (margin + interest).
"""


def recognized_amount(collected, cogs, total_profit):
    """Profit recognized on a deal once `collected` has been received."""
    if total_profit <= 0:
        return 0.0
    above_cost = collected - cogs
    if above_cost <= 0:
        return 0.0
    return min(above_cost, total_profit)


def recognized_delta(collected_before, collected_after, cogs, total_profit):
    """Profit recognized during a period = recognized(after) - recognized(before)."""
    return (
        recognized_amount(collected_after, cogs, total_profit)
        - recognized_amount(collected_before, cogs, total_profit)
    )


def split_recognized(recognized, margin, total_profit):
    """Split a recognized-profit amount into (margin_part, interest_part),
    proportional to the deal's margin vs interest composition."""
    if total_profit <= 0:
        return 0.0, 0.0
    margin_part = recognized * (margin / total_profit)
    return margin_part, recognized - margin_part
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest nasiya365.tests.test_recognition -v`
Expected: PASS (13 tests)

If imports fail on the host, run inside the dev backend container instead:
`docker exec nasiya365-frappe-backend-1 python3 -m unittest nasiya365.tests.test_recognition -v`

- [ ] **Step 5: Commit**

```bash
git add nasiya365/api/recognition.py nasiya365/tests/test_recognition.py
git commit -m "feat(pnl): pure cost-recovery recognition math + unit tests"
```

---

### Task 2: Add «Возмещение затрат» to Merchant Settings profit_method

**Files:**
- Modify: `nasiya365/nasiya365/doctype/merchant_settings/merchant_settings.json` (the `profit_method` field, ~line 278)

**Interfaces:**
- Produces: `profit_method` accepts the value `Возмещение затрат`.

- [ ] **Step 1: Edit the field options + description**

In `merchant_settings.json`, find the `profit_method` field and change its `options` and `description`:

```json
        {
            "default": "По оплате (касса)",
            "description": "«По оплате (касса)» = прибыль признаётся по мере поступления денег. «При продаже (начисление)» = вся прибыль сделки признаётся в момент продажи. «Возмещение затрат» = прибыль по сделке появляется только после того, как собранные деньги покрыли её себестоимость.",
            "fieldname": "profit_method",
            "fieldtype": "Select",
            "label": "Метод признания прибыли",
            "options": "По оплате (касса)\nПри продаже (начисление)\nВозмещение затрат"
        },
```

- [ ] **Step 2: Verify the JSON is valid**

Run: `python3 -c "import json; json.load(open('nasiya365/nasiya365/doctype/merchant_settings/merchant_settings.json')); print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add nasiya365/nasiya365/doctype/merchant_settings/merchant_settings.json
git commit -m "feat(pnl): add cost-recovery option to profit_method"
```

---

### Task 3: Implement `_compute_cost_recovery` and wire into `compute_profit`

**Files:**
- Modify: `nasiya365/api/profit.py` (add import; add `_compute_cost_recovery`; add branch in `compute_profit`)

**Interfaces:**
- Consumes (from Task 1): `recognized_delta`, `split_recognized`.
- Consumes (existing): `_compute_accrual`, `_cogs_for_sale_item`, `_plan_profit`, `_so_profit`, `_user_branches`.
- Produces: `_compute_cost_recovery(from_date, to_date, branch) -> dict` with keys:
  - Standard (drive `_apply_basis` → net_profit): `cash_margin`, `financed_margin`, `interest_income` — all set to **recognized** values. Plus `cash_revenue`, `cash_cogs`, `financed_revenue`, `financed_cogs` carried from accrual (sales-side; harmless, not shown in cost-recovery layout).
  - Раздел 1 (potential/sales): `sales_cash_revenue`, `sales_cash_cogs`, `sales_cash_margin`, `sales_financed_revenue`, `sales_financed_cogs`, `sales_financed_margin`, `sales_total_margin`, `sales_interest`, `potential_profit`.
  - Раздел 2 (recognized): `collected` (total collected in period). (`recognized_margin` = `total_margin` and `recognized_interest` = `interest_income` after `_apply_basis`; `recognized_profit` = their sum — the report derives these.)
  - `from_date`, `to_date`.

- [ ] **Step 1: Write the failing integration test**

Append to `nasiya365/tests/test_recognition.py` a Frappe-backed test that exercises the wiring on seeded data. Create `nasiya365/tests/test_cost_recovery_engine.py`:

```python
import unittest

import frappe
from frappe.utils import add_days, today

from nasiya365.api.profit import _compute_cost_recovery


class TestCostRecoveryEngine(unittest.TestCase):
    """Seeds one financed deal + payments in a transaction, then rolls back."""

    def setUp(self):
        frappe.db.begin()

    def tearDown(self):
        frappe.db.rollback()

    def _seed_plan_with_payment(self, principal, financed, interest, cogs, pay_amount, pay_date):
        # Minimal Stock Entry so COGS resolves by IMEI.
        imei = "TESTIMEI000001"
        se = frappe.new_doc("Stock Entry")
        se.append("items", {"imei": imei, "amount": cogs, "expense": 0, "qty": 1})
        se.insert(ignore_permissions=True, ignore_mandatory=True)
        plan = frappe.new_doc("Installment Plan")
        plan.imei = imei
        plan.principal_amount = principal
        plan.financed_amount = financed
        plan.total_interest = interest
        plan.total_amount = principal + interest
        plan.start_date = pay_date
        plan.status = "Активный"
        plan.contract_status = "Активный"
        plan.insert(ignore_permissions=True, ignore_mandatory=True)
        pt = frappe.new_doc("Payment Transaction")
        pt.reference_doctype = "Installment Plan"
        pt.reference_name = plan.name
        pt.amount = pay_amount
        pt.status = "Завершен"
        pt.payment_date = pay_date
        pt.insert(ignore_permissions=True, ignore_mandatory=True)
        return plan

    def test_down_payment_below_cost_recognizes_zero(self):
        # phone: principal 650, cogs 620, interest 90 -> total profit 120
        # down 350 collected today < 620 -> recognized 0
        d = today()
        self._seed_plan_with_payment(650, 300, 90, 620, 350, d)
        comp = _compute_cost_recovery(d, d, None)
        self.assertAlmostEqual(comp["financed_margin"], 0.0, places=2)
        self.assertAlmostEqual(comp["interest_income"], 0.0, places=2)
        self.assertAlmostEqual(comp["collected"], 350.0, places=2)
        # Раздел 1 still shows the sale's potential
        self.assertAlmostEqual(comp["sales_financed_margin"], 30.0, places=2)
        self.assertAlmostEqual(comp["potential_profit"], 120.0, places=2)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker exec nasiya365-frappe-backend-1 bench --site my.nasiya365.uz run-tests --app nasiya365 --module nasiya365.tests.test_cost_recovery_engine`
Expected: FAIL — `ImportError: cannot import name '_compute_cost_recovery'`

(If the module-path form is rejected, run: `docker exec nasiya365-frappe-backend-1 bash -lc "cd /home/frappe/frappe-bench && bench --site my.nasiya365.uz run-tests --app nasiya365 --module nasiya365.tests.test_cost_recovery_engine"`.)

- [ ] **Step 3: Add the import**

In `nasiya365/api/profit.py`, near the top imports add:

```python
from nasiya365.api.recognition import recognized_delta, split_recognized
```

- [ ] **Step 4: Implement `_compute_cost_recovery`**

Add this function to `profit.py` (after `_compute_accrual`):

```python
# ── COST RECOVERY BASIS ──────────────────────────────────────────────────────

def _plan_cogs(plan):
    return _cogs_for_sale_item(plan.imei, plan.get("stock_entry"), plan.get("start_date"))


def _collected_before(rdt, rn, from_date):
    """Sum of completed payments on a deal strictly before the period start."""
    return flt(frappe.db.sql(
        """SELECT COALESCE(SUM(amount), 0) FROM `tabPayment Transaction`
           WHERE docstatus < 2 AND status = 'Завершен'
             AND reference_doctype = %s AND reference_name = %s
             AND DATE(payment_date) < %s""",
        (rdt, rn, from_date))[0][0])


def _compute_cost_recovery(from_date, to_date, branch):
    """Cost-recovery recognition. Раздел 1 (sales/potential) reuses accrual;
    Раздел 2 (recognized) is driven by cumulative collections vs COGS per deal."""
    unrestricted, user_branches = _user_branches()

    # ── Раздел 1: sales made in the period (potential) — reuse accrual as-is.
    sales = _compute_accrual(from_date, to_date, branch)

    # ── Раздел 2: recognition from collections.
    # Deals with >= 1 completed payment inside the window, with each deal's
    # branch resolved the same way as _compute_cash.
    window = frappe.db.sql(
        """
        SELECT pt.reference_doctype AS rdt, pt.reference_name AS rn,
               SUM(pt.amount) AS win,
               CASE
                 WHEN pt.reference_doctype = 'Installment Plan' THEN (
                     SELECT so.branch FROM `tabSales Order` so
                     JOIN `tabInstallment Plan` ip ON ip.sales_order = so.name
                     WHERE ip.name = pt.reference_name LIMIT 1)
                 WHEN pt.reference_doctype = 'Sales Order' THEN (
                     SELECT branch FROM `tabSales Order` WHERE name = pt.reference_name LIMIT 1)
               END AS branch
        FROM `tabPayment Transaction` pt
        WHERE pt.docstatus < 2 AND pt.status = 'Завершен'
          AND pt.reference_name IS NOT NULL
          AND DATE(pt.payment_date) BETWEEN %s AND %s
        GROUP BY pt.reference_doctype, pt.reference_name
        """,
        (from_date, to_date), as_dict=True,
    )

    rec_margin_cash = rec_margin_fin = rec_interest = 0.0
    collected_total = 0.0
    plan_cache, so_cache = {}, {}

    for w in window:
        b = w.branch
        if branch and b != branch:
            continue
        if not unrestricted and (b not in user_branches):
            continue

        before = _collected_before(w.rdt, w.rn, from_date)
        after = before + flt(w.win)

        if w.rdt == "Installment Plan":
            plan = plan_cache.get(w.rn)
            if plan is None:
                plan = frappe.db.get_value(
                    "Installment Plan", w.rn,
                    ["imei", "principal_amount", "financed_amount", "total_interest",
                     "total_amount", "contract_status", "stock_entry", "start_date"],
                    as_dict=True)
                plan_cache[w.rn] = plan
            if not plan or plan.contract_status == "Отменен":
                continue
            cogs = _plan_cogs(plan)
            margin = (flt(plan.principal_amount) or flt(plan.financed_amount)) - cogs
            interest = flt(plan.total_interest)
            total_profit = margin + interest
            delta = recognized_delta(before, after, cogs, total_profit)
            m_part, i_part = split_recognized(delta, margin, total_profit)
            rec_margin_fin += m_part
            rec_interest += i_part
            collected_total += flt(w.win)

        elif w.rdt == "Sales Order":
            so = so_cache.get(w.rn)
            if so is None:
                so = frappe.db.get_value(
                    "Sales Order", w.rn, ["name", "total_amount"], as_dict=True)
                so_cache[w.rn] = so
            if not so:
                continue
            cogs = _cogs_for_sales_order(so.name)
            margin = flt(so.total_amount) - cogs
            total_profit = margin  # cash sale: no interest
            delta = recognized_delta(before, after, cogs, total_profit)
            m_part, _ = split_recognized(delta, margin, total_profit)
            rec_margin_cash += m_part
            collected_total += flt(w.win)

    # potential (Раздел 1) totals derived from accrual
    sales_total_margin = flt(sales["cash_margin"]) + flt(sales["financed_margin"])
    sales_interest = flt(sales["interest_income"])

    comp = dict(sales)  # carry cash/financed revenue+cogs (sales side)
    comp.update({
        "from_date": str(from_date),
        "to_date": str(to_date),
        # Раздел 1 (potential)
        "sales_cash_revenue": flt(sales["cash_revenue"]),
        "sales_cash_cogs": flt(sales["cash_cogs"]),
        "sales_cash_margin": flt(sales["cash_margin"]),
        "sales_financed_revenue": flt(sales["financed_revenue"]),
        "sales_financed_cogs": flt(sales["financed_cogs"]),
        "sales_financed_margin": flt(sales["financed_margin"]),
        "sales_total_margin": sales_total_margin,
        "sales_interest": sales_interest,
        "potential_profit": sales_total_margin + sales_interest,
        # Раздел 2 (recognized) — these DRIVE _apply_basis / net_profit
        "cash_margin": rec_margin_cash,
        "financed_margin": rec_margin_fin,
        "interest_income": rec_interest,
        "collected": collected_total,
    })
    return comp
```

- [ ] **Step 5: Wire into `compute_profit`**

In `compute_profit`, change the method dispatch:

```python
    if method.startswith("Возмещение"):
        comp = _compute_cost_recovery(from_date, to_date, branch)
    elif method.startswith("При продаже"):
        comp = _compute_accrual(from_date, to_date, branch)
    else:
        comp = _compute_cash(from_date, to_date, branch)
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `docker exec nasiya365-frappe-backend-1 bench --site my.nasiya365.uz run-tests --app nasiya365 --module nasiya365.tests.test_cost_recovery_engine`
Expected: PASS

- [ ] **Step 7: Re-run the pure tests to confirm no regression**

Run: `python3 -m unittest nasiya365.tests.test_recognition -v`
Expected: PASS (13 tests)

- [ ] **Step 8: Commit**

```bash
git add nasiya365/api/profit.py nasiya365/tests/test_cost_recovery_engine.py
git commit -m "feat(pnl): cost-recovery recognition engine (_compute_cost_recovery)"
```

---

### Task 4: Two-section report layout for cost recovery

**Files:**
- Modify: `nasiya365/nasiya365/report/profit_and_loss_summary/profit_and_loss_summary.py`

**Interfaces:**
- Consumes: `compute_profit(...)` dict with the cost-recovery keys from Task 3.
- Produces: the report returns the two-section layout when `p["profit_method"]` starts with «Возмещение»; otherwise the existing 11-row layout is unchanged.

- [ ] **Step 1: Add the cost-recovery view branch**

In `execute()`, right after `p = compute_profit(from_date, to_date, branch)` and the `columns`/`row` definitions, insert:

```python
    if (p.get("profit_method") or "").startswith("Возмещение"):
        data = _cost_recovery_rows(p, row)
        report_summary = [
            {"label": _("Признанная прибыль"),
             "value": frappe.utils.fmt_money(p["net_profit"], currency="USD"),
             "indicator": "Green" if p["net_profit"] >= 0 else "Red"},
            {"label": _("Потенциал сделок периода"),
             "value": frappe.utils.fmt_money(p["potential_profit"], currency="USD")},
            {"label": _("Собрано"),
             "value": frappe.utils.fmt_money(p["collected"], currency="USD")},
        ]
        return columns, data, None, None, report_summary
```

- [ ] **Step 2: Add the `_cost_recovery_rows` helper**

Add this module-level function to the report file:

```python
def _cost_recovery_rows(p, row):
    recognized_margin = flt(p["cash_margin"]) + flt(p["financed_margin"])
    recognized_interest = flt(p["interest_income"])
    recognized_profit = recognized_margin + recognized_interest
    cogs_recovered = flt(p["collected"]) - recognized_profit
    return [
        row(_("РАЗДЕЛ 1. Продажи за период (по факту продажи)"), 0, bold=1),
        row(_("Наличные — продажа"), p["sales_cash_revenue"], indent=1),
        row(_("Наличные — себестоимость"), -p["sales_cash_cogs"], indent=1),
        row(_("Наличные — маржа"), p["sales_cash_margin"], indent=1),
        row(_("Рассрочка — продажа"), p["sales_financed_revenue"], indent=1),
        row(_("Рассрочка — себестоимость"), -p["sales_financed_cogs"], indent=1),
        row(_("Рассрочка — маржа"), p["sales_financed_margin"], indent=1),
        row(_("Итого маржа товара"), p["sales_total_margin"], bold=1),
        row(_("Процентный доход (потенциальный)"), p["sales_interest"], indent=1),
        row(_("Потенциальная прибыль сделок"), p["potential_profit"], bold=1),
        row(_("РАЗДЕЛ 2. Признано за период (возмещение затрат)"), 0, bold=1),
        row(_("Собрано денег"), p["collected"], indent=1),
        row(_("Возмещение себестоимости"), -cogs_recovered, indent=1),
        row(_("Признанная прибыль"), recognized_profit, bold=1),
        row(_("    в т.ч. маржа"), recognized_margin, indent=1),
        row(_("    в т.ч. проценты"), recognized_interest, indent=1),
        row(_("Операционные расходы"), -p["expenses"], indent=1),
        row(_("ЧИСТАЯ ПРИБЫЛЬ (признанная)"), p["net_profit"], bold=1),
    ]
```

- [ ] **Step 3: Sanity-check the report renders (dev)**

Run in the container (prints columns/data length without error):
`docker exec nasiya365-frappe-backend-1 bench --site my.nasiya365.uz execute nasiya365.nasiya365.report.profit_and_loss_summary.profit_and_loss_summary.execute --kwargs "{'filters': {'from_date': '2026-07-01', 'to_date': '2026-07-31'}}"`
Expected: no exception; returns a tuple. (Dev data uses the default method, so this exercises the non-cost-recovery path — it must still work.)

- [ ] **Step 4: Commit**

```bash
git add nasiya365/nasiya365/report/profit_and_loss_summary/profit_and_loss_summary.py
git commit -m "feat(pnl): two-section report view for cost-recovery method"
```

---

### Task 5: Enable method on prod merchant + verify on live data

**Files:** none (data + verification only). **This task changes prod data — get the user's explicit go-ahead before Step 1.**

- [ ] **Step 1: (User-gated) Set the method**

After deploy, in prod Merchant Settings set «Метод признания прибыли» = «Возмещение затрат». (Do this in the UI, or with the user's approval via a one-off console call — do not batch-write silently.)

- [ ] **Step 2: Verify in the browser on prod**

Open the P&L report for a period. Confirm:
- Two sections render (Раздел 1 / Раздел 2).
- Раздел 1 shows приход/продажа/маржа split for наличные and рассрочка + потенциальные проценты.
- Раздел 2 «Признанная прибыль» equals hand calc: for each deal collected in the period, `max(0, collected_to_date − COGS) − max(0, collected_before − COGS)`, summed.
- For the known deal INST-2026-00364 ($620 cogs, $350 collected) recognized contribution = $0.

- [ ] **Step 3: Confirm the other methods are unaffected**

Temporarily switch method back to «По оплате (касса)» and confirm the report returns to the original 11-row layout with the same numbers as before this change. Switch back to «Возмещение затрат». (One-click, reversible.)

---

## Self-Review

**Spec coverage:**
- §2 method (cost recovery, variant A, per deal) → Task 1 (math) + Task 3 (engine). ✓
- §3 algorithm (cumulative collections, delta, proportional split) → Task 1 + Task 3. ✓
- §4/§5 two-section report, superset, cash/installment split, 11-row mapping → Task 4. ✓
- §6 add method / don't touch cash & accrual / reuse COGS → Task 2, Task 3 (Global Constraints). ✓
- §7 safety (read-only, reversible) → Task 5 Step 3. ✓
- §8 tests (8 scenarios) → Task 1 covers pure-math scenarios 1–5,7; Task 3 covers the down-payment integration scenario; Task 5 covers branch/permission + live verification. ✓
- §9 out of scope (balance, currency) → not implemented, per constraints. ✓

**Placeholder scan:** No TBD/TODO; all steps carry real code. ✓

**Type consistency:** `recognized_delta`/`split_recognized` signatures match between Task 1 definition and Task 3 use; dict keys produced in Task 3 (`sales_*`, `collected`, `potential_profit`, recognized `cash_margin`/`financed_margin`/`interest_income`) match those consumed in Task 4. ✓
