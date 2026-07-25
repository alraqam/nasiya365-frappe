# IMEI Search (Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let staff find phones/plans by IMEI across five entry points (installment-plan list + link dropdown + global search, the BNPL panel, the overdue-collector page, and the Sales report).

**Architecture:** IMEI already lives on `Installment Plan` as a top-level field, so plan-based search is a metadata change plus one read-only whitelisted query. The BNPL panel gets a small search box that calls that query and renders mini-cards reusing the existing payment dialog. The overdue-collector page extends its existing client-side filter. The Sales report gains an optional IMEI filter that matches both the financed branch (`ip.imei`) and the cash branch (`sales_order_item.imei`).

**Tech Stack:** Frappe v16 (Python + MariaDB), jQuery Desk pages, Frappe Script Reports, `FrappeTestCase`.

## Global Constraints

- **Match mode:** partial substring, digits-only input, minimum 3 digits. Shorter terms return `[]` without a DB query.
- **Because the term is digits-only, it can never contain LIKE wildcards** (`%` `_` `\`) — no LIKE-escaping is needed anywhere.
- **Branch scoping:** every plan query reuses `_user_branch_clause("ip")` from `nasiya365/api/bnpl_dashboard.py`. Tests run as Administrator (unrestricted → empty clause).
- **BNPL panel result:** mini-card with client · status · plan № · product+IMEI · remaining balance, plus buttons **«Открыть план»** and **«Принять платёж»** (reuse `openPaymentDialog`).
- **No schema change:** do not add columns or indexes. `imei` already exists on Installment Plan.
- **Site name:** `my.nasiya365.uz`.
- **Tests:** `FrappeTestCase` (auto-rollback). Run on dev with `allow_tests` enabled. Test module: `nasiya365.tests.test_imei_search`.
- **Frappe caches doctype meta client-side and page JS is served from source** — after JS/JSON changes: `bench --site my.nasiya365.uz clear-cache`, restart, and hard-refresh the browser.

## File Structure

- Modify `nasiya365/nasiya365/doctype/installment_plan/installment_plan.json` — add `imei` to `search_fields`.
- Modify `nasiya365/api/bnpl_dashboard.py` — add `_sanitize_imei_term`, `search_plans_by_imei`, and `ip.imei` to the two collector list queries.
- Modify `nasiya365/nasiya365/page/bnpl_control_center/bnpl_control_center.js` — panel IMEI search box + results.
- Modify `nasiya365/public/css/bnpl_control_center.css` — styles for the IMEI block.
- Modify `nasiya365/nasiya365/page/overdue_collector/overdue_collector.js` — extend the client filter + placeholder.
- Modify `nasiya365/nasiya365/report/sales_report/sales_report.py` and `sales_report.js` — IMEI filter + column.
- Create `nasiya365/tests/test_imei_search.py` — Python tests.

---

### Task 0: Dev test environment

**Files:** none (environment only)

- [ ] **Step 1: Start the dev stack**

Bring the containers up (compose file as used for dev). Confirm:

Run: `docker ps --format '{{.Names}}'`
Expected: backend / mariadb / redis containers listed.

- [ ] **Step 2: Enable tests on the site**

Run: `docker compose exec backend bench --site my.nasiya365.uz set-config allow_tests true`
Expected: no error.

- [ ] **Step 3: Sanity-run the existing suite**

Run: `docker compose exec backend bench --site my.nasiya365.uz run-tests --module nasiya365.tests.test_data_import`
Expected: tests execute (pass/fail both fine — we only confirm the runner works).

> Note: all later `run-tests` / `bench` commands assume this `docker compose exec backend` prefix. If your dev bench runs on the host, drop the prefix.

---

### Task 1: IMEI in Installment Plan `search_fields`

Covers the installment-plan list search, the link-field dropdown, and the global awesomebar.

**Files:**
- Modify: `nasiya365/nasiya365/doctype/installment_plan/installment_plan.json`

**Interfaces:**
- Produces: nothing code-level; enables `imei` in Frappe's standard search for Installment Plan.

- [ ] **Step 1: Edit `search_fields`**

Change the line:

```json
 "search_fields": "customer,customer_name",
```

to:

```json
 "search_fields": "customer,customer_name,imei",
```

- [ ] **Step 2: Migrate**

Run: `docker compose exec backend bench --site my.nasiya365.uz migrate`
Expected: completes; no `ALTER TABLE` on `tabInstallment Plan` (field already exists).

- [ ] **Step 3: Verify the meta picked it up**

Run:
```bash
docker compose exec -T backend bench --site my.nasiya365.uz console <<'PY'
print(frappe.get_meta("Installment Plan").search_fields)
PY
```
Expected: output contains `imei` (e.g. `customer,customer_name,imei`).

- [ ] **Step 4: Commit**

```bash
git add nasiya365/nasiya365/doctype/installment_plan/installment_plan.json
git commit -m "feat(imei): add imei to Installment Plan search_fields"
```

---

### Task 2: `_sanitize_imei_term` helper (pure)

**Files:**
- Modify: `nasiya365/api/bnpl_dashboard.py`
- Test: `nasiya365/tests/test_imei_search.py`

**Interfaces:**
- Produces: `_sanitize_imei_term(imei) -> str | None` — returns digits-only term, or `None` when fewer than 3 digits.

- [ ] **Step 1: Write the failing test**

Create `nasiya365/tests/test_imei_search.py`:

```python
import frappe
from frappe.tests.utils import FrappeTestCase

from nasiya365.api.bnpl_dashboard import _sanitize_imei_term


class TestSanitizeImeiTerm(FrappeTestCase):
    def test_keeps_full_imei(self):
        self.assertEqual(_sanitize_imei_term("356938035643809"), "356938035643809")

    def test_strips_spaces_and_letters(self):
        self.assertEqual(_sanitize_imei_term("  643 809 "), "643809")
        self.assertEqual(_sanitize_imei_term("imei:356938"), "356938")

    def test_too_short_returns_none(self):
        self.assertIsNone(_sanitize_imei_term("12"))
        self.assertIsNone(_sanitize_imei_term("ab1"))  # 1 digit

    def test_empty_and_none(self):
        self.assertIsNone(_sanitize_imei_term(""))
        self.assertIsNone(_sanitize_imei_term(None))

    def test_wildcards_are_stripped(self):
        # digits-only means LIKE metachars can never survive
        self.assertEqual(_sanitize_imei_term("35%_69\\3"), "35693")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `docker compose exec backend bench --site my.nasiya365.uz run-tests --module nasiya365.tests.test_imei_search`
Expected: FAIL / ImportError — `_sanitize_imei_term` does not exist yet.

- [ ] **Step 3: Implement the helper**

At the top of `nasiya365/api/bnpl_dashboard.py`, add `import re` if not present. Add near the other small helpers (e.g. after `_safe_limit`):

```python
_IMEI_MIN_DIGITS = 3


def _sanitize_imei_term(imei):
    """Digits-only IMEI search term, or None when too short to search.

    Cashiers paste spaces/labels and only remember a tail of the IMEI, so we
    keep just the digits. Terms under 3 digits are refused (too broad). Because
    the result is digits-only, it can never contain LIKE wildcards.
    """
    digits = re.sub(r"\D", "", imei or "")
    if len(digits) < _IMEI_MIN_DIGITS:
        return None
    return digits
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `docker compose exec backend bench --site my.nasiya365.uz run-tests --module nasiya365.tests.test_imei_search`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add nasiya365/api/bnpl_dashboard.py nasiya365/tests/test_imei_search.py
git commit -m "feat(imei): add _sanitize_imei_term helper with tests"
```

---

### Task 3: `search_plans_by_imei` whitelisted query

**Files:**
- Modify: `nasiya365/api/bnpl_dashboard.py`
- Test: `nasiya365/tests/test_imei_search.py`

**Interfaces:**
- Consumes: `_sanitize_imei_term`, `_user_branch_clause("ip")`, `_safe_limit`.
- Produces: `search_plans_by_imei(imei, limit=20) -> list[dict]` with keys `name, customer, customer_name, status, remaining_balance, product_name, imei`.

- [ ] **Step 1: Write the failing test**

Append to `nasiya365/tests/test_imei_search.py`:

```python
from nasiya365.api.bnpl_dashboard import search_plans_by_imei


def _make_plan(imei, customer_name="Тест Клиент", status="Активный",
               product_name="iPhone 13 Test", remaining_balance=420, docstatus=0,
               start_date=None):
    """Insert a minimal Installment Plan row without running validate/hooks.

    search_plans_by_imei reads via raw SQL, so a bare row is enough and avoids
    the plan's heavy validate()/generate_schedule().
    """
    plan = frappe.get_doc({
        "doctype": "Installment Plan",
        "customer_name": customer_name,
        "imei": imei,
        "status": status,
        "product_name": product_name,
        "remaining_balance": remaining_balance,
        "docstatus": docstatus,
        "principal_amount": 1000,
        "start_date": start_date or frappe.utils.today(),
    })
    plan.name = frappe.generate_hash("imei-test", 10)
    plan.db_insert()
    return plan.name


class TestSearchPlansByImei(FrappeTestCase):
    def test_partial_match_finds_plan(self):
        name = _make_plan("356938035643809")
        found = [r["name"] for r in search_plans_by_imei("643809")]
        self.assertIn(name, found)

    def test_excludes_non_matching_plan(self):
        match = _make_plan("356938035643809")
        other = _make_plan("351756051523700")
        found = [r["name"] for r in search_plans_by_imei("643809")]
        self.assertIn(match, found)
        self.assertNotIn(other, found)

    def test_short_term_returns_empty(self):
        _make_plan("356938035643809")
        self.assertEqual(search_plans_by_imei("12"), [])

    def test_returns_expected_fields(self):
        _make_plan("356938035643809", customer_name="Иван", product_name="iPhone X")
        row = search_plans_by_imei("643809")[0]
        for key in ("name", "customer", "customer_name", "status",
                    "remaining_balance", "product_name", "imei"):
            self.assertIn(key, row)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `docker compose exec backend bench --site my.nasiya365.uz run-tests --module nasiya365.tests.test_imei_search`
Expected: FAIL — `search_plans_by_imei` not defined.

- [ ] **Step 3: Implement the method**

Add to `nasiya365/api/bnpl_dashboard.py` (near the other `@frappe.whitelist()` list methods):

```python
@frappe.whitelist()
def search_plans_by_imei(imei, limit=20):
    """Installment plans whose IMEI contains the given digits (partial match).

    Digits-only, minimum 3 digits, branch-scoped, read-only. Used by the BNPL
    panel's «Найти по IMEI» box.
    """
    term = _sanitize_imei_term(imei)
    if not term:
        return []

    branch_clause, branch_params = _user_branch_clause("ip")
    like = f"%{term}%"
    return frappe.db.sql(
        f"""
        SELECT ip.name, ip.customer, ip.customer_name, ip.status,
               ip.remaining_balance, ip.product_name, ip.imei
        FROM `tabInstallment Plan` ip
        WHERE ip.imei LIKE %s
          {branch_clause}
        ORDER BY ip.modified DESC
        LIMIT %s
        """,
        (like, *branch_params, _safe_limit(limit, 20, 50)),
        as_dict=True,
    )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `docker compose exec backend bench --site my.nasiya365.uz run-tests --module nasiya365.tests.test_imei_search`
Expected: PASS (all `TestSearchPlansByImei` + `TestSanitizeImeiTerm`).

- [ ] **Step 5: Restart so the whitelisted method is served**

Run: `docker compose exec backend bench --site my.nasiya365.uz clear-cache && docker compose restart backend`
Expected: backend restarts.

- [ ] **Step 6: Commit**

```bash
git add nasiya365/api/bnpl_dashboard.py nasiya365/tests/test_imei_search.py
git commit -m "feat(imei): add search_plans_by_imei whitelisted query with tests"
```

---

### Task 4: BNPL panel IMEI search box

**Files:**
- Modify: `nasiya365/nasiya365/page/bnpl_control_center/bnpl_control_center.js`
- Modify: `nasiya365/public/css/bnpl_control_center.css`

**Interfaces:**
- Consumes: `nasiya365.api.bnpl_dashboard.search_plans_by_imei`, existing `openPaymentDialog(row)`, global `nasiya365._fmt_money`, global `flt`.

- [ ] **Step 1: Add the mount point to the container**

In the constructor's container template, add the IMEI search div right after the CTA row:

```javascript
        this.container = $(`
            <div class="bnpl-dashboard">
                <div class="bnpl-cta-row"></div>
                <div class="bnpl-imei-search"></div>
                <div class="bnpl-needs-attention"></div>
```

- [ ] **Step 2: Call the renderer in `render()`**

In `render()`, add the call after `this.renderCtaRow();`:

```javascript
	render() {
		this.renderCtaRow();
		this.renderImeiSearch();
		this.refreshAll();
	}
```

- [ ] **Step 3: Add the two render methods**

Add these methods to the `BnplControlCenter` class (e.g. right after `renderCtaRow()`):

```javascript
	renderImeiSearch() {
		const wrap = this.container.find(".bnpl-imei-search").empty();
		const box = $(`
			<div class="bnpl-imei-block">
				<div class="bnpl-imei-head">
					<span class="bnpl-imei-title">${__("Найти по IMEI")}</span>
					<span class="bnpl-imei-hint-top">${__("частичный поиск · минимум 3 цифры")}</span>
				</div>
				<div class="bnpl-imei-field">
					<input type="text" class="form-control bnpl-imei-input" inputmode="numeric"
						autocomplete="off" placeholder="${__("Введите IMEI или его часть")}" />
				</div>
				<div class="bnpl-imei-results"></div>
			</div>
		`).appendTo(wrap);

		const input = box.find(".bnpl-imei-input");
		const results = box.find(".bnpl-imei-results");

		const run = frappe.utils.debounce(() => {
			const term = (input.val() || "").replace(/\D/g, "");
			if (term.length < 3) {
				results.html(`<div class="bnpl-empty">${__("Введите минимум 3 цифры IMEI.")}</div>`);
				return;
			}
			frappe.call({
				method: "nasiya365.api.bnpl_dashboard.search_plans_by_imei",
				args: { imei: term },
				callback: (r) => this.renderImeiResults(results, r.message || []),
			});
		}, 300);

		input.on("input", run);
	}

	renderImeiResults(results, rows) {
		results.empty();
		if (!rows.length) {
			results.html(`<div class="bnpl-empty">${__("По этому IMEI рассрочек не найдено.")}</div>`);
			return;
		}
		rows.forEach((row) => {
			const node = $(`
				<div class="bnpl-imei-card">
					<div class="bnpl-imei-card-main">
						<div class="bnpl-imei-card-title">
							<span>${frappe.utils.escape_html(row.customer_name || row.customer || "—")}</span>
							<span class="bnpl-imei-status">${frappe.utils.escape_html(row.status || "")}</span>
							<span class="bnpl-imei-plan">${frappe.utils.escape_html(row.name)}</span>
						</div>
						<div class="bnpl-imei-card-sub">${frappe.utils.escape_html(row.product_name || "")} · IMEI ${frappe.utils.escape_html(row.imei || "")}</div>
					</div>
					<div class="bnpl-imei-card-money">
						<div class="bnpl-imei-money-label">${__("Остаток")}</div>
						<div class="bnpl-imei-money-val">${nasiya365._fmt_money(flt(row.remaining_balance))}</div>
					</div>
					<div class="bnpl-imei-card-actions">
						<button class="btn btn-default btn-sm btn-open">${__("Открыть план")}</button>
						<button class="btn btn-primary btn-sm btn-pay">${__("Принять платёж")}</button>
					</div>
				</div>
			`).appendTo(results);
			node.find(".btn-open").on("click", () =>
				frappe.set_route("Form", "Installment Plan", row.name));
			node.find(".btn-pay").on("click", () => this.openPaymentDialog({
				customer: row.customer,
				installment_plan: row.name,
				amount_due: flt(row.remaining_balance),
			}));
		});
	}
```

- [ ] **Step 4: Add styles**

Append to `nasiya365/public/css/bnpl_control_center.css`:

```css
/* ——— IMEI quick search ——— */
.bnpl-imei-block {
	border: 1px solid rgba(59, 130, 246, 0.28);
	border-radius: 14px;
	background: linear-gradient(135deg, rgba(59, 130, 246, 0.06) 0%, var(--card-bg) 60%);
	padding: 16px;
	margin-bottom: 16px;
	box-shadow: 0 4px 18px rgba(0, 0, 0, 0.04);
}
.bnpl-imei-head { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; }
.bnpl-imei-title { font-size: 15px; font-weight: 800; letter-spacing: -0.01em; }
.bnpl-imei-hint-top { font-size: 12px; opacity: 0.7; margin-left: auto; }
.bnpl-imei-input {
	font-size: 16px; font-weight: 600; letter-spacing: 0.03em;
	font-variant-numeric: tabular-nums;
}
.bnpl-imei-results { margin-top: 12px; display: flex; flex-direction: column; gap: 10px; }
.bnpl-imei-card {
	display: flex; align-items: center; gap: 14px; flex-wrap: wrap;
	border: 1px solid var(--border-color); border-radius: 10px;
	background: var(--card-bg); padding: 12px 14px;
	box-shadow: 0 4px 18px rgba(0, 0, 0, 0.04);
}
.bnpl-imei-card-main { flex: 1 1 260px; min-width: 220px; }
.bnpl-imei-card-title { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; font-weight: 700; font-size: 15px; }
.bnpl-imei-status { font-size: 11px; font-weight: 700; padding: 3px 9px; border-radius: 999px; background: rgba(59, 130, 246, 0.1); color: #2563eb; }
.bnpl-imei-plan { font-size: 11.5px; opacity: 0.6; font-weight: 600; font-variant-numeric: tabular-nums; }
.bnpl-imei-card-sub { font-size: 12.5px; opacity: 0.8; margin-top: 4px; font-variant-numeric: tabular-nums; }
.bnpl-imei-card-money { text-align: right; }
.bnpl-imei-money-label { font-size: 10.5px; text-transform: uppercase; letter-spacing: 0.04em; opacity: 0.7; }
.bnpl-imei-money-val { font-size: 17px; font-weight: 800; font-variant-numeric: tabular-nums; }
.bnpl-imei-card-actions { display: flex; gap: 7px; flex-shrink: 0; }
@media (max-width: 640px) {
	.bnpl-imei-card-money { text-align: left; }
	.bnpl-imei-card-actions { width: 100%; }
	.bnpl-imei-card-actions .btn { flex: 1; }
}
```

- [ ] **Step 5: Manual verification in the browser**

1. `docker compose exec backend bench --site my.nasiya365.uz clear-cache`
2. Open the **Финансовый центр** page in a fresh tab, hard-refresh (Cmd/Ctrl+Shift+R).
3. Type a known IMEI tail → a mini-card appears with client, status, plan №, product+IMEI, remaining.
4. «Открыть план» opens the Installment Plan form. «Принять платёж» opens the payment dialog.
5. Type `12` → hint «минимум 3 цифры». Type a non-existent IMEI → «не найдено».

Expected: all five behave as described.

- [ ] **Step 6: Commit**

```bash
git add nasiya365/nasiya365/page/bnpl_control_center/bnpl_control_center.js nasiya365/public/css/bnpl_control_center.css
git commit -m "feat(imei): add IMEI quick-search box to BNPL panel"
```

---

### Task 5: Overdue-collector page — search by client or IMEI

**Files:**
- Modify: `nasiya365/api/bnpl_dashboard.py` (`get_due_today_list`, `get_overdue_list`)
- Modify: `nasiya365/nasiya365/page/overdue_collector/overdue_collector.js`

**Interfaces:**
- Produces: `imei` key on each row returned by `get_due_today_list` and `get_overdue_list`.

- [ ] **Step 1: Add `ip.imei` to the collector queries**

In `nasiya365/api/bnpl_dashboard.py`, for **every** SQL inside `get_due_today_list` and `get_overdue_list` that selects from `` `tabInstallment Plan` ip ``:
- add `ip.imei AS imei,` to the SELECT list (next to `ip.product_name`);
- add `ip.imei` to that query's `GROUP BY` (required under `ONLY_FULL_GROUP_BY`).

Example (financed/overdue query):

```sql
            ip.name AS installment_plan,
            ...
            COALESCE(NULLIF(TRIM(ip.product_name), ''), '') AS product_name,
            ip.imei AS imei,
            ...
        GROUP BY ip.name, ip.customer, customer_name, ip.product_name, ip.imei
```

Apply the same two edits to the `get_due_today_list` query (its `GROUP BY ip.name, ip.customer, cp.full_name, ip.product_name` → append `, ip.imei`).

- [ ] **Step 2: Verify the API returns `imei`**

Run:
```bash
docker compose exec backend bench --site my.nasiya365.uz console <<'PY'
from nasiya365.api.bnpl_dashboard import get_overdue_list, get_due_today_list
for fn in (get_overdue_list, get_due_today_list):
    rows = fn(limit=1)
    print(fn.__name__, "imei" in (rows[0] if rows else {"imei": None}))
PY
```
Expected: both print `True` (or the list is empty — if so, confirm the key exists by reading the SELECT you edited).

- [ ] **Step 3: Extend the client-side filter + placeholder**

In `overdue_collector.js`:

Change the placeholder:

```javascript
					<input class="form-control" placeholder="Поиск: клиент или IMEI" />
```

Replace **both** filter lines (in the two `frappe.call` callbacks):

```javascript
					rows = rows.filter((row) => (row.customer_name || row.customer || "").toLowerCase().includes(this.query));
```

with:

```javascript
					rows = rows.filter((row) =>
						((row.customer_name || row.customer || "") + " " + (row.imei || ""))
							.toLowerCase()
							.includes(this.query),
					);
```

- [ ] **Step 4: Manual verification**

1. `docker compose exec backend bench --site my.nasiya365.uz clear-cache`, hard-refresh the **Сбор просрочки** page.
2. Type part of a client name → list filters as before.
3. Type part of an IMEI that belongs to an overdue plan → the matching contract stays.

Expected: both client and IMEI narrow the list.

- [ ] **Step 5: Commit**

```bash
git add nasiya365/api/bnpl_dashboard.py nasiya365/nasiya365/page/overdue_collector/overdue_collector.js
git commit -m "feat(imei): search overdue collector by client or IMEI"
```

---

### Task 6: Sales report — IMEI filter + column

**Files:**
- Modify: `nasiya365/nasiya365/report/sales_report/sales_report.py`
- Modify: `nasiya365/nasiya365/report/sales_report/sales_report.js`
- Test: `nasiya365/tests/test_imei_search.py`

**Interfaces:**
- Consumes: existing `execute(filters)`; adds `filters["imei"]`.
- Produces: an `imei` column and IMEI filtering across both sale branches.

- [ ] **Step 1: Write the failing test (financed branch)**

Append to `nasiya365/tests/test_imei_search.py`:

```python
from nasiya365.nasiya365.report.sales_report.sales_report import execute as sales_report_execute


class TestSalesReportImeiFilter(FrappeTestCase):
    def _live_plan(self, imei):
        return _make_plan(imei, docstatus=1, status="Активный",
                          start_date=frappe.utils.today())

    def test_filter_keeps_matching_plan(self):
        match = self._live_plan("356938035643809")
        other = self._live_plan("351756051523700")
        data = sales_report_execute({"imei": "643809", "sale_type": "Рассрочка"})[1]
        names = [r["doc_name"] for r in data]
        self.assertIn(match, names)
        self.assertNotIn(other, names)

    def test_column_includes_imei(self):
        self._live_plan("356938035643809")
        data = sales_report_execute({"imei": "643809", "sale_type": "Рассрочка"})[1]
        self.assertEqual(data[0]["imei"], "356938035643809")
```

- [ ] **Step 2: Run to verify it fails**

Run: `docker compose exec backend bench --site my.nasiya365.uz run-tests --module nasiya365.tests.test_imei_search`
Expected: FAIL — no `imei` filtering yet / `KeyError: 'imei'`.

- [ ] **Step 3: Read the `imei` filter in `execute`**

In `sales_report.py`, in `execute()`, after the existing filter reads:

```python
    sale_type = filters.get("sale_type")  # "", "Наличные", "Рассрочка"
    imei = (filters.get("imei") or "").strip()
    imei_like = f"%{imei}%"
```

- [ ] **Step 4: Add the `imei` column**

In the `columns` list, add right after the `product_name` column:

```python
        {"label": _("IMEI"), "fieldname": "imei", "fieldtype": "Data", "width": 140},
```

- [ ] **Step 5: Filter + emit imei on the cash branch**

In the cash-sales block, add an IMEI predicate and select the item's imei. Update the cash query's WHERE to include (only when filtering):

```python
        cash_imei = " AND EXISTS (SELECT 1 FROM `tabSales Order Item` soi WHERE soi.parent = so.name AND soi.imei LIKE %s)" if imei else ""
```

Add `cash_imei` into the f-string WHERE (next to `{so_branch}`), and add `(*([imei_like] if imei else []))` to that query's params tuple. Then in the `for so in cash:` loop, fetch and emit imei alongside `product`:

```python
            imei_val = frappe.db.get_value(
                "Sales Order Item", {"parent": so.name, "idx": 1}, "imei"
            )
            data.append({
                ...
                "product_name": product or "—",
                "imei": imei_val or "",
                ...
            })
```

- [ ] **Step 6: Filter + emit imei on the financed branch**

In the installment block, add the predicate to the plans query WHERE (next to `{plan_branch_clause}` / `{expl}`):

```python
        imei_sql = " AND ip.imei LIKE %s" if imei else ""
```

Add `{imei_sql}` to the WHERE and append `imei_like` to that query's params tuple **only when `imei`** is set (mirror how `expl_params` is spread). Then in the `for p in plans:` loop add to the appended dict:

```python
                "imei": p.imei or "",
```

- [ ] **Step 7: Add the report filter control**

In `sales_report.js`, add to the `filters` array:

```javascript
		{
			fieldname: "imei",
			label: __("IMEI"),
			fieldtype: "Data",
		},
```

- [ ] **Step 8: Run the test to verify it passes**

Run: `docker compose exec backend bench --site my.nasiya365.uz run-tests --module nasiya365.tests.test_imei_search`
Expected: PASS (financed-branch filter + column).

- [ ] **Step 9: Manually verify the cash branch**

Pick an existing cash Sales Order that has an item IMEI (or note none exist). Open **Продажи (Sales Report)**, set IMEI to that value.

Run (console sanity, substitute a real IMEI):
```bash
docker compose exec backend bench --site my.nasiya365.uz console <<'PY'
from nasiya365.nasiya365.report.sales_report.sales_report import execute
data = execute({"imei": "REPLACE_WITH_REAL_IMEI"})[1]
print(len(data), [(r["sale_type"], r["imei"]) for r in data][:5])
PY
```
Expected: only rows whose IMEI contains the term; each row shows the IMEI column.

- [ ] **Step 10: Commit**

```bash
git add nasiya365/nasiya365/report/sales_report/sales_report.py nasiya365/nasiya365/report/sales_report/sales_report.js nasiya365/tests/test_imei_search.py
git commit -m "feat(imei): add IMEI filter and column to Sales report"
```

---

### Task 7: Full-suite green + cleanup

**Files:** none (verification)

- [ ] **Step 1: Run the whole new module**

Run: `docker compose exec backend bench --site my.nasiya365.uz run-tests --module nasiya365.tests.test_imei_search`
Expected: all tests PASS.

- [ ] **Step 2: Confirm no stray schema change**

Run: `git diff main --stat`
Expected: only the files listed in **File Structure** changed; no migration/patch files, no ALTER.

- [ ] **Step 3: (Optional) disable tests again on dev**

If the dev site should not keep tests enabled:
Run: `docker compose exec backend bench --site my.nasiya365.uz set-config allow_tests false`

---

## Self-Review

**Spec coverage:**
- §5 Part 1 (search_fields) → Task 1. ✅
- §5 Part 2 (search_plans_by_imei + sanitization) → Tasks 2–3. ✅
- §5 Part 3 (panel UI) → Task 4. ✅
- §5 Part 4 (overdue collector) → Task 5. ✅
- §5 Part 5 (sales report filter + column, both branches) → Task 6. ✅
- §6 tests (sanitization, search, branch scoping via Administrator, sales report) → Tasks 2, 3, 6. ✅
- §7 deploy notes (migrate, restart, clear-cache) → folded into Task steps. ✅

**Placeholder scan:** the only `REPLACE_WITH_REAL_IMEI` is an explicit manual-verification input for cash data that only exists on the live DB — not a code placeholder.

**Type consistency:** `_sanitize_imei_term` (Task 2) → returns `str|None`, consumed by `search_plans_by_imei` (Task 3) and the panel JS digit-strip (Task 4) mirrors it. `search_plans_by_imei` returns keys `name, customer, customer_name, status, remaining_balance, product_name, imei` — consumed verbatim by `renderImeiResults` (Task 4). `_make_plan` helper (Task 3) is reused by Task 6’s `_live_plan`. Consistent.

**Known-fragility notes (honest):**
- Cash-branch IMEI filter is verified manually, not by an automated test (building a submitted Sales Order + item + COGS fixture is heavy and brittle). The financed branch is covered automatically.
- The collector API `imei` key is verified via console, not a unit test (needs live overdue data).
