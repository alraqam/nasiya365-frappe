# Foundation brief: P&L report → new custom Page («Прибыль и поступления»)

**Дата:** 2026-07-28
**Статус:** research only — READ-ONLY, no code changed
**Область:** redesign the `Profit and Loss Summary` Script Report as a Frappe custom Page,
mirroring the existing `bnpl-control-center` / `overdue-collector` page pattern.
**Companion doc:** `docs/task-redesign-profit-and-loss-report.md` (the task spec this brief
grounds in real file:line references) and `docs/superpowers/specs/2026-07-28-pnl-cost-recovery-design.md`
(explains *why* "Возмещение затрат" computes the way it does).

---

## A. The existing custom-page pattern

Reference implementation read in full: `nasiya365/nasiya365/page/bnpl_control_center/`
(4 files) and, for cross-check, `nasiya365/nasiya365/page/overdue_collector/overdue_collector.json`.

### A1. Files a custom page consists of

| File | Purpose |
|---|---|
| `bnpl_control_center.json` | The `Page` doctype record — metadata Frappe uses to register the route, title, module, and (optionally) role restriction. No layout info lives here. |
| `bnpl_control_center.js` | 100% of the page's behavior: DOM construction, `frappe.call` wiring, event handlers, rendering. This is what Frappe's build pipeline auto-discovers and serves when the route is opened. |
| `bnpl_control_center.py` | Only a `get_context(context)` hook (2 lines of real code) — Frappe's `bench make-page` boilerplate for when a Page doctype is resolved as a **website** route (`/bnpl-control-center`, no `/app` prefix) rather than the desk route (`/app/bnpl-control-center`). Not involved in normal desk usage; present but effectively inert for a desk-only page. |
| `__init__.py` | Empty except a copyright header — required so the folder is a Python package (module discovery). |
| No `.css` file in the page directory at all — see A4. |

### A2. `Page` doctype JSON — exact fields

`nasiya365/nasiya365/page/bnpl_control_center/bnpl_control_center.json:1-19`:

```json
{
    "content": null,
    "creation": "2026-03-27 00:00:00.000000",
    "docstatus": 0,
    "doctype": "Page",
    "idx": 0,
    "modified": "2026-03-27 00:00:00.000000",
    "modified_by": "Administrator",
    "module": "nasiya365",
    "name": "bnpl-control-center",
    "owner": "Administrator",
    "page_name": "bnpl-control-center",
    "roles": [],
    "script": "",
    "standard": "Yes",
    "style": "",
    "system_page": 0,
    "title": "Nasiya365 — Финансовый центр"
}
```

Key fields:
- `name` / `page_name` — the route slug (`/app/bnpl-control-center`). Must be kebab-case and match the folder name pattern (folder is the snake_case form: `bnpl_control_center`).
- `module`: `"nasiya365"` — ties the page into the app's module for fixtures/export.
- `standard: "Yes"` — this is a filesystem-defined (developer-authored) page, not one created ad-hoc in the Desk UI.
- `title` — shown in the page header and browser tab; Russian text is fine (matches the rest of the app).
- `roles` — **empty array in both existing example pages** (`bnpl_control_center.json:13`, `overdue_collector.json:13`). An empty `roles` table on a `Page` record means Frappe applies no page-level role restriction — any user who can reach the Desk can open the route. `content`, `script`, `style` are legacy fields from the old "Page Builder" UI and are unused/null here — all real behavior lives in the sibling `.js` file.

**Important divergence to flag for the new page:** the *current* `Profit and Loss Summary` **Report** doctype explicitly restricts access —
`nasiya365/nasiya365/report/profit_and_loss_summary/profit_and_loss_summary.json:21-24`:
```json
"roles": [
    {"role": "System Manager"},
    {"role": "Nasiya365 Admin"}
]
```
If the new Page is built by copying the `bnpl-control-center`/`overdue-collector` pattern verbatim (`roles: []`), it would **widen access** relative to today's report — any desk user, not just System Manager/Nasiya365 Admin, could open it. Confirmed by reading `nasiya365/api/profit.py` in full: `get_profit_summary` (`nasiya365/api/profit.py:609-611`) has no `frappe.only_for(...)` / `has_role` gate of its own — it only applies **branch**-level row filtering via `_user_branches()` / `_is_unrestricted()` (`nasiya365/api/profit.py:177-184`, backed by `nasiya365/permissions.py:5` `_UNRESTRICTED_ROLES = {"System Manager", "Nasiya365 Admin"}`). So today the Report's `roles: [System Manager, Nasiya365 Admin]` is the **only** access gate on this data. **Recommendation: the new Page's `.json` must explicitly set the same `roles` list**, not leave it empty like the two precedent pages — otherwise P&L visibility silently expands to every desk user.

### A3. How the page JS loads and renders

`nasiya365/nasiya365/page/bnpl_control_center/bnpl_control_center.js:1-10`:
```js
frappe.pages["bnpl-control-center"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Nasiya365 — Финансовый центр"),
		single_column: true,
	});
	const view = new nasiya365.BnplControlCenter(page);
	view.render();
};
```
- Frappe calls `on_page_load(wrapper)` once, the first time the route is visited (not on every re-visit — Frappe caches page instances by default; there is no `on_page_show`/refresh hook used here, so re-entering the route does not re-fetch data unless the class does it explicitly).
- `frappe.ui.make_app_page(...)` is the standard Desk chrome builder: gives a title bar, a `page.main` container, indicator/menu slots, etc. `single_column: true` means no default sidebar.
- The class pattern (`nasiya365.BnplControlCenter`, registered on the `nasiya365` namespace via `frappe.provide("nasiya365")`, `bnpl_control_center.js:12`) builds its own skeleton DOM in the constructor (`bnpl_control_center.js:20-38`) with empty placeholder `<div>`s per section (`bnpl-cta-row`, `bnpl-kpi-grid`, `bnpl-cashflow`, `bnpl-split-grid` with two `bnpl-panel`s, etc.), appended once to `this.page.main`. `render()` (`bnpl_control_center.js:40-44`) then calls per-section `renderXxx(data)` methods that `.empty()` and repopulate their own sub-container — i.e. **cards/sections are plain jQuery-built DOM fragments assembled with template literals**, not a component framework. Each `renderXxx` is independently re-callable (used both on first load and on `refreshAll()`, `bnpl_control_center.js:195-198`).
- The "cards" pattern (`renderKpiGrid`, `bnpl_control_center.js:455-483`) is directly reusable for the P&L's 3 top cards: a `.empty()`'d grid container, `.forEach` over a small array of `{id, label, value, subtext, tone, trend, route}` objects, each rendered as one templated `<div class="...-card ...-card--{tone}">`, optionally clickable via `frappe.set_route(...)`.

### A4. How CSS is loaded — NOT a per-page file

There is **no** `bnpl_control_center.css` anywhere in the repo. All page-specific CSS lives in one
shared, hand-maintained, app-wide stylesheet: `nasiya365/public/css/nasiya365.bundle.css`
(767 lines). Its header comment (`nasiya365.bundle.css:1-15`) explains why:

> Compiled by Frappe's esbuild into a content-hashed file... hooks.py references it by logical
> name: `app_include_css = ["nasiya365.bundle.css"]`. NOTE: content is INLINED here (not
> `@import`-ed). Frappe's esbuild/postcss cannot resolve `@import "./sibling.css"` (it processes
> the entry file in an isolated temp dir), and this app's dev image has no node to compile a
> `.bundle.scss` — so a single self-contained `.css` is the one form that builds on prod AND is
> served raw on dev. **Formerly: `bnpl_control_center.css` + `installment_plan_wizard.css`.**

So the *original* design did use per-page CSS files, but that broke the production esbuild
pipeline and was consolidated (see recent commits `dde345a`, `c9994be`, `c60a65a` in `git log`).
The BNPL page's rules now live inline under a banner comment,
`nasiya365.bundle.css:17-18`: `/* ===================== bnpl_control_center ===================== */`,
followed by every `.bnpl-*` class the JS references (`.bnpl-dashboard`, `.bnpl-kpi-card`, etc.,
through line ~691), plus a later unrelated section for `cash_handover.js` (`.bnpl-row-disputed`,
line 767).

`nasiya365/hooks.py:24-32`:
```python
# Bundled (content-hashed) so a new deploy changes the asset URL and browsers
# fetch fresh code without a manual cache clear. Source files are imported from
# public/js/nasiya365.bundle.js.
app_include_js = ["nasiya365.bundle.js"]

# Bundled for the same cache-busting reason; entry: public/css/nasiya365.bundle.css.
app_include_css = ["nasiya365.bundle.css"]
```
Both are loaded on **every** Desk page (`app_include_js`/`app_include_css` = global head includes),
content-hashed at `bench build --production` time (per `Dockerfile:36`: `RUN bench build --production`).
There is no `page_js`/page-specific CSS hook in `hooks.py` at all — `doctype_js`
(`hooks.py:41-49`) only exists for **DocType form/list** scripts (Sales Order, Payment
Transaction, etc.), unrelated to Page routes.

`nasiya365.bundle.js` (`nasiya365/public/js/nasiya365.bundle.js:1-18`) is a *separate* concept
from the page's own `.js`: it's a small global side-effect bundle (`installment_calculator.js`,
`pwa_register.js`, `list_column_picker.js`, `version_nudge.js`, `idle_logout.js` — 5 imports)
loaded on every page. The page-specific `bnpl_control_center.js` is **not** part of this bundle
and needs no `hooks.py` entry — Frappe's own build/route-serving mechanism auto-discovers and
serves `page/<name>/<name>.js` when the matching Desk route is opened, purely from the file
living in the `page/<name>/` folder with the doctype record's `name` matching.

**Conclusion for the new page: any new page-specific CSS must be added as a new banner-commented
section inside the existing `nasiya365/public/css/nasiya365.bundle.css`** (there is no working
alternative in this codebase's build setup) — e.g. `/* ===== profit_and_loss (page) ===== */`
appended after the existing sections, following the `.bnpl-*`-style naming convention (something
like `.pnl-*`).

### A5. Backend calls, filters, loading/empty states

`frappe.call` pattern, e.g. `bnpl_control_center.js:116-120`:
```js
frappe.call({
    method: "nasiya365.api.bnpl_dashboard.search_by_imei",
    args: { imei: term },
    callback: (r) => this.renderImeiResults(results, r.message || [], term),
});
```
Always `method` (dotted Python path) + `args` + `callback(r)` reading `r.message`; errors are
checked via `r.exc` on chained calls (`bnpl_control_center.js:224,233,242,251` inside
`refreshAllLegacy()`) rather than a `.fail()`/`error:` handler — a truthy `r.exc` triggers
`showFatalLoadError()` (`bnpl_control_center.js:272-285`), which shows one dismissable
`.bnpl-banner.bnpl-banner--risk` prepended to the container and then renders every section with
empty data (so stale numbers never linger).

**No visible loading spinner/skeleton exists in this page today** — `refreshAll()` /
`refreshAllLegacy()` chain 4 sequential `frappe.call`s (`bnpl_control_center.js:220-269`) with no
interim "loading" UI; the grids simply stay whatever they were until the final callback repaints
them. This means the P&L redesign's spec requirement for an explicit loading/skeleton state
(`docs/task-redesign-profit-and-loss-report.md:358-362`) has **no existing precedent to copy** —
it must be built new (e.g. a simple `frappe.dom.freeze()`/spinner or CSS skeleton div toggled
around the single `frappe.call`).

There's no dedicated "filters bar" on this page (it's a dashboard, not a filtered report) — for
filter-bar precedent, see section C below (the Report's `filters:` config) since that's what
needs to be reproduced as custom page-header controls (Date/Date/Link + "Сформировать отчёт"
button), not anything in `bnpl_control_center.js`.

### A6. Routing / entry points

- Desk URL: `/app/bnpl-control-center` (derived straight from `page_name`).
- Workspace entry: `nasiya365/nasiya365/workspace/nasiya365/nasiya365.json` — both a `links[]`
  entry (`"link_type": "Page", "link_to": "bnpl-control-center"`) and a `shortcuts[]` entry
  (`"type": "Page", "link_to": "bnpl-control-center"`).
- Programmatic navigation elsewhere in the app: `frappe.set_route("overdue-collector")`
  (`bnpl_control_center.js:502`), i.e. a bare page-name route (no `"Page"` prefix needed, unlike
  `frappe.set_route("Form", "Sales Order", name)` / `frappe.set_route("List", "Sales Order")`).

**Migration note:** today's `Profit and Loss Summary` Report is linked from the same workspace
JSON with `"link_type": "Report", "is_query_report": 1` (`nasiya365.json:172`, and a second
`shortcuts[]` entry `"type": "Report", "report_ref_doctype": "Installment Plan"`). Converting to
a Page means these two workspace entries must switch `link_type`/`type` from `"Report"` to
`"Page"` and `link_to` to the new page's slug — otherwise the workspace card/shortcut will 404 or
point at the old, unstyled report.

**Export note:** a Script Report gets CSV/Excel/PDF export, column sort/filter, and "Refresh" for
free from Frappe's generic report view chrome. A custom Page gets **none of that automatically**.
The task spec explicitly requires "не удалять экспорт, фильтрацию и обновление" (§14/§18,
`docs/task-redesign-profit-and-loss-report.md:403,569`) — the new page must implement its own
export button (e.g. build a CSV client-side from the same adapter output, or keep the old
`Profit and Loss Summary` Script Report registered/hidden-from-menu purely as an export/API
fallback) and its own explicit "Обновить"/refresh button, since none of that is inherited for
free the way it is on a Report.

---

## B. Backend data contract — `compute_profit` for method **"Возмещение затрат"**

Read in full: `nasiya365/api/profit.py` (618 lines) — `compute_profit` (`:227-241`), `_apply_basis`
(`:244-273`), `_compute_cost_recovery` (`:470-573`), `get_profit_summary` (`:609-611`).

### B1. Exact signature

`nasiya365/api/profit.py:609-611`:
```python
@frappe.whitelist()
def get_profit_summary(from_date, to_date, branch=None):
    return compute_profit(from_date, to_date, branch)
```
Plain positional/keyword args, `branch` optional (`None` = no branch filter, but per-user branch
restriction from `_user_branches()` still applies regardless of the `branch` arg —
`profit.py:177-184`). Callable from JS as
`frappe.call({ method: "nasiya365.api.profit.get_profit_summary", args: { from_date, to_date, branch } })`.

### B2. How the return dict is assembled for cost recovery

`compute_profit` (`profit.py:227-241`) dispatches on `Merchant Settings.profit_method`
(`method.startswith("Возмещение")` → `_compute_cost_recovery`), then unconditionally adds
`comp["expenses"]` and pipes the whole dict through `_apply_basis`.

`_compute_cost_recovery` (`profit.py:470-573`):
1. Раздел 1 ("potential"): `sales = _compute_accrual(from_date, to_date, branch)` — reused
   verbatim, returns `{from_date, to_date, cash_revenue, cash_cogs, cash_margin,
   financed_revenue, financed_cogs, financed_margin, interest_income}` (`profit.py:441-451`),
   where every `*_cogs` is a **positive magnitude** (`cash_cogs = cash_revenue - cash_margin` at
   `profit.py:445`, not negated).
2. `comp = dict(sales)` (`profit.py:553`) — copies all 9 accrual keys as a starting point.
3. `comp.update({...})` (`profit.py:554-572`) adds the `sales_*` / `potential_profit` /
   `collected` keys **and overwrites** `cash_margin`, `financed_margin`, `interest_income`,
   `from_date`, `to_date` with the *recognized* (Раздел 2, collections-driven) values.
4. Back in `compute_profit`, `comp["expenses"]` is added, then `_apply_basis` adds
   `profit_basis`, `profit_method`, `total_margin`, `interest_in_profit`, `expenses_in_profit`,
   `gross_profit`, `net_profit` — where `total_margin = comp["cash_margin"] + comp["financed_margin"]`
   (`profit.py:247`) uses the **already-overwritten recognized** values, not the sales ones.

### B3. ⚠️ Critical gotcha — duplicate-named keys mean two different things

Because step 3 above overwrites `cash_margin` / `financed_margin` / `interest_income` in place,
**the final dict has both a "sales" (potential, Раздел 1) and a "recognized" (Раздел 2) version of
the same concept, under different-looking key names that are easy to confuse**:

| Concept | "Sales / potential" key (Раздел 1, accrual-style) | "Recognized" key (Раздел 2, collections-driven) |
|---|---|---|
| Cash margin | `sales_cash_margin` | `cash_margin` *(overwritten!)* |
| Financed margin | `sales_financed_margin` | `financed_margin` *(overwritten!)* |
| Interest | `sales_interest` | `interest_income` *(overwritten!)* |
| Total margin | `sales_total_margin` | `total_margin` *(derived in `_apply_basis`, = recognized `cash_margin + financed_margin`)* |
| Cash revenue/cogs | `sales_cash_revenue` / `sales_cash_cogs` | `cash_revenue` / `cash_cogs` *(NOT overwritten — happen to equal the `sales_*` values since they come from the same `sales` dict and are never touched again)* |
| Financed revenue/cogs | `sales_financed_revenue` / `sales_financed_cogs` | `financed_revenue` / `financed_cogs` *(same — not overwritten, duplicate of `sales_*`)* |

**Using the bare `cash_margin`/`financed_margin`/`interest_income`/`total_margin` for Table 1 (the
"sales made this period" table) would silently show the wrong (recognized, not potential) numbers.**
Table 1 must exclusively use the `sales_*`-prefixed keys and `potential_profit`; Table 2 must
exclusively use the bare `cash_margin`/`financed_margin`/`interest_income`/`total_margin` (which
in cost-recovery mode *are* the recognized figures) plus `collected`/`gross_profit`/`net_profit`.

### B4. Full key list present when `profit_method` = "Возмещение затрат"

All keys the task asked to confirm are **present** for this method (none absent):

| Key | Meaning |
|---|---|
| `from_date`, `to_date` | Echoed period boundaries (`str(date)`), overwritten at `profit.py:555-556` to the same values as input. |
| `sales_cash_revenue` | Cash-sale revenue for Sales Orders placed in the period (Раздел 1). |
| `sales_cash_cogs` | Matching COGS, **positive** magnitude. |
| `sales_cash_margin` | `sales_cash_revenue − sales_cash_cogs`. |
| `sales_financed_revenue` | Installment-plan principal for plans started in the period (Раздел 1). |
| `sales_financed_cogs` | Matching COGS, **positive** magnitude. |
| `sales_financed_margin` | `sales_financed_revenue − sales_financed_cogs`. |
| `sales_total_margin` | `sales_cash_margin + sales_financed_margin` (`profit.py:550`). |
| `sales_interest` | Total contractual interest on plans started in the period (potential, not yet collected) (`profit.py:551`). |
| `potential_profit` | `sales_total_margin + sales_interest` (`profit.py:566`) — "if every new deal were paid in full." |
| `collected` | Sum of all completed Payment Transactions in the period, any deal (`profit.py:571`, accumulated at `:531,547`). |
| `cash_margin` | **Recognized** cash-sale margin this period (collections-driven, cost-recovery formula via `recognized_delta`/`split_recognized`) — overwritten at `profit.py:568`. |
| `financed_margin` | **Recognized** installment margin this period — overwritten at `profit.py:569`. |
| `interest_income` | **Recognized** interest this period — overwritten at `profit.py:570`. |
| `gross_profit` | From `_apply_basis` (`profit.py:264-272`): `total_margin + interest_income` unless `profit_basis = "Только маржа"` (then just `total_margin`). |
| `net_profit` | `gross_profit − expenses_in_profit` (`profit.py:271`). |
| `expenses` | Period operating expenses, always computed (`profit.py:240`, via `_period_expenses`, `:276-294`) regardless of whether they count toward `net_profit`. |
| `interest_in_profit` | Bool-ish: whether `profit_basis` folds interest into `gross_profit` (0 only for `"Только маржа"`). |
| `expenses_in_profit` | Bool-ish: whether `profit_basis` folds expenses into `net_profit` (1 only for `"Чистая прибыль"`, the default). |
| `profit_basis` | Merchant Settings value: `"Только маржа"` \| `"Валовая прибыль"` \| `"Чистая прибыль"` (default). |
| `profit_method` | Merchant Settings value, here `"Возмещение затрат"`. |
| `cash_revenue` | Duplicate of `sales_cash_revenue` (carried through from `dict(sales)`, never overwritten). |
| `cash_cogs` | Duplicate of `sales_cash_cogs`. |
| `financed_revenue` | Duplicate of `sales_financed_revenue`. |
| `financed_cogs` | Duplicate of `sales_financed_cogs`. |
| `total_margin` | **Recognized** total margin = `cash_margin + financed_margin` (post-overwrite), computed in `_apply_basis` — NOT the same as `sales_total_margin`. |

There is **no** `cost_recovery` / `cogs_recovered` key returned by the backend at all — see D
below, it must be derived client-side using the exact formula already used by the current report.

---

## C. Report filters + roles

`nasiya365/nasiya365/report/profit_and_loss_summary/profit_and_loss_summary.js:1-31`:

| fieldname | label | fieldtype | default | reqd |
|---|---|---|---|---|
| `from_date` | "С даты" | Date | `frappe.datetime.add_months(frappe.datetime.get_today(), -1)` | 1 |
| `to_date` | "По дату" | Date | `frappe.datetime.get_today()` | 1 |
| `branch` | "Филиал" | Link (`options: "Branch"`) | none | 0 |

Also a `formatter(value, row, column, data, default_formatter)` (`:24-30`) that bolds any row with
`data.bold` truthy — this is how the existing report renders its "Итого"/section-header rows bold
in the generic report-view grid; irrelevant to a custom page (bolding will just be CSS there).

`nasiya365/nasiya365/report/profit_and_loss_summary/profit_and_loss_summary.json:1-25`:
- `report_type: "Script Report"`, `ref_doctype: "Installment Plan"`, `module: "nasiya365"`.
- `roles: [{"role": "System Manager"}, {"role": "Nasiya365 Admin"}]` (`:21-24`) — this is the
  access gate discussed in A2; must be replicated on the new Page.
- `is_standard: "Yes"`, `columns: []` and `filters: []` in the JSON itself (both actually defined
  client-side in the `.js`, not the doctype record — normal for Script Reports).

The task-spec labels ("Дата с" / "Дата по" / "Филиал", `docs/task-redesign-profit-and-loss-report.md:96-102`)
are a slight rename from the current "С даты"/"По дату"/"Филиал" — flagged for whoever implements,
not something to resolve in this brief.

---

## D. Key-mapping table (UI element → backend key → source → formula)

Format: `UI element → compute_profit key(s) → source → formula/notes`. Method = "Возмещение
затрат" throughout (matches Merchant Settings on this deployment per
`docs/superpowers/specs/2026-07-28-pnl-cost-recovery-design.md`).

### D1. Top cards (§5 of the task spec)

| UI element | Key | Source | Formula / notes |
|---|---|---|---|
| «Поступило от клиентов» | `collected` | direct | Sum of completed Payment Transactions in period, any doctype reference. |
| «Заработанная прибыль» | `net_profit` | direct | `gross_profit − expenses_in_profit`, basis-dependent. |
| «Будущая прибыль по новым сделкам» | `potential_profit` | direct | `sales_total_margin + sales_interest`. |

### D2. Table 1 «Продажи, оформленные за период» (§6)

| Row | Column | Key(s) | Formula / notes |
|---|---|---|---|
| Наличные | Продажи | `sales_cash_revenue` | direct |
| Наличные | Себестоимость | `sales_cash_cogs` | direct, **already positive** — do not negate for display (spec §6: "На UI себестоимость показывать положительным числом, не изменяя серверное значение"). |
| Наличные | Маржа товара | `sales_cash_margin` | direct |
| Наличные | Проценты по рассрочке | *(none)* | No key — client renders `—` (em dash), cash sales carry no interest. |
| Наличные | Общая прибыль | `sales_cash_margin` | Same value as Маржа товара (cash has no interest to add). |
| Рассрочка | Продажи | `sales_financed_revenue` | direct |
| Рассрочка | Себестоимость | `sales_financed_cogs` | direct, already positive. |
| Рассрочка | Маржа товара | `sales_financed_margin` | direct |
| Рассрочка | Проценты по рассрочке | `sales_interest` | direct |
| Рассрочка | Общая прибыль | **DERIVED**: `sales_financed_margin + sales_interest` | No single key — client-side sum of 2 existing values, per spec §6 formula ("Рассрочка — маржа + Процентный доход"). |
| Итого | Продажи | **DERIVED**: `sales_cash_revenue + sales_financed_revenue` | No combined key exists; simple sum. |
| Итого | Себестоимость | **DERIVED**: `sales_cash_cogs + sales_financed_cogs` | No combined key exists; simple sum, positive. |
| Итого | Маржа товара | `sales_total_margin` | direct — already equals the sum above. |
| Итого | Проценты по рассрочке | `sales_interest` | direct — same value as the Рассрочка row (cash contributes none). |
| Итого | Общая прибыль | `potential_profit` | direct — already equals `sales_total_margin + sales_interest`. |

### D3. Table 2 «Поступления и заработанная прибыль» (§7)

| Row | Key(s) | Formula / notes |
|---|---|---|
| Поступило от клиентов | `collected` | direct |
| Покрытие себестоимости | **DERIVED, no direct key**: `collected − (cash_margin + financed_margin + interest_income)` | This exact formula is already implemented server-side today in the *old* report builder — `nasiya365/nasiya365/report/profit_and_loss_summary/profit_and_loss_summary.py:15-18`: `recognized_margin = cash_margin + financed_margin; recognized_interest = interest_income; recognized_gross = recognized_margin + recognized_interest; cogs_recovered = collected − recognized_gross`. **Note this recognized_gross is always margin+interest regardless of `profit_basis`** — it is NOT the same computation as `gross_profit` (which is basis-dependent). The new adapter must reproduce this exact formula, not reuse `gross_profit` for it. Displayed as a positive magnitude (spec: "не выглядит как убыток"). |
| Заработанная товарная маржа | **DERIVED**: `cash_margin + financed_margin` | Sum of the two recognized-margin keys (see B3 — these are the *overwritten*, Раздел 2 values, not `sales_*`). No single `recognized_margin` key is returned by the API; the old report computes this same local variable at `profit_and_loss_summary.py:15` without exposing it. |
| Заработанные проценты | `interest_income` | direct — the recognized (overwritten) value. |
| Валовая прибыль | `gross_profit` | direct — basis-dependent (see B4). |
| Операционные расходы | `expenses` | direct — always the period total, independent of whether it's subtracted into `net_profit`. |
| Заработанная прибыль за период | `net_profit` | direct |

### D4. Values with no backend key at all (fully client-side)

- **"Общая прибыль" (Рассрочка row, Table 1)** — sum, see D2.
- **"Итого — Продажи/Себестоимость" (Table 1)** — sums, see D2.
- **"Покрытие себестоимости" (Table 2)** — formula, see D3; this is the single most
  error-prone derived value since its formula differs subtly from `gross_profit`.
- **"Заработанная товарная маржа" (Table 2)** — sum, see D3.
- **§8 info block** ("База расчёта прибыли...") — driven directly by `profit_basis`,
  `interest_in_profit`, `expenses_in_profit` (all direct keys), just needs client-side
  string selection, no arithmetic.
- **§13 "Обновлено: <timestamp>"** — **not returned by the backend at all** (`compute_profit`
  only returns the *period* `from_date`/`to_date`, not a "generated at" wall-clock timestamp).
  Must be captured client-side at the moment the `frappe.call` callback resolves (e.g.
  `frappe.datetime.now_datetime()` or `new Date()`), independent of the adapter.
- **Cash sales' "Проценты" cell** = literal em dash `—`, not `0` — spec §16 test data
  distinguishes this from installment's `$0.00` (when `sales_interest` happens to be 0,
  it must still render as `$0.00`, only the *absent-for-cash-sales* case renders `—`).
  This is a display-branch, not a data lookup — cash has no interest key at all,
  installment's `sales_interest` key is always present (possibly `0`).

---

## E. Recommended file structure for the new page

### E1. Page files (mirrors `bnpl_control_center` 1:1)

```
nasiya365/nasiya365/page/profit_and_loss/
├── __init__.py                    # empty + copyright header, matches existing pages
├── profit_and_loss.json           # Page doctype record — set roles explicitly (A2), title "Прибыль и поступления"
├── profit_and_loss.py             # get_context() boilerplate only, matches bnpl_control_center.py
└── profit_and_loss.js             # frappe.pages["profit-and-loss"].on_page_load — DOM/render orchestration only
```
(Slug choice `profit-and-loss` / folder `profit_and_loss` is illustrative — any kebab/snake pair
consistent with `bnpl-control-center`/`bnpl_control_center` works; final naming is an
implementation decision, not part of this brief.)

### E2. Adapter / view-model — kept separate from the page JS, plain and framework-free

Per task spec §15 ("Создать отдельный adapter/view model") and the E4 finding below (no JS test
runner exists), the adapter must be **pure, Frappe-free JS** so it can be tested with plain
`node`, independent of the Desk page:

```
nasiya365/public/js/pnl_adapter.js        # pure function(s): raw compute_profit dict -> {summary, sales, recognized} view-model (matches the shape sketched in task spec §15)
```
This file should have **zero references to `frappe`, `$`, `window`, or DOM** — only plain JS
(functions + arithmetic + null/undefined guards), so it is importable both by:
- `profit_and_loss.js` (the Desk page renderer, which calls the adapter then builds DOM), and
- a standalone Node test script (see E4) with no Frappe runtime required.

Because there's no existing JS bundler/module system wired for page-specific code (see A4 — only
the *global* `nasiya365.bundle.js`/`.css` go through esbuild; individual `page/<name>/<name>.js`
files are loaded by Frappe's own per-page mechanism, not user-authored `import`s), the simplest
integration is either:
(a) write `pnl_adapter.js` as an IIFE/global (`window.nasiya365pnl = {...}` style, matching the
`frappe.provide("nasiya365")` global-namespace convention already used in
`bnpl_control_center.js:12,20`) and load it via `hooks.py`'s `page_js` dict... **but no such hook
key is used anywhere in this repo's `hooks.py` today** (only `doctype_js`/`doctype_list_js`
exist) — so the safer, precedent-following option is
(b) add `pnl_adapter.js` to the app-wide bundle imports in `nasiya365/public/js/nasiya365.bundle.js`
(`nasiya365.bundle.js:14-18`, one more `import "./pnl_adapter.js";` line, side-effect style like
the other 5), which makes it available globally before the page JS runs on any Desk page — same
mechanism already used for `installment_calculator.js`. This decision (module system for the
adapter) should be confirmed during implementation planning, not assumed here.

### E3. CSS

Per A4, there is no working per-page CSS file mechanism in this codebase's build — new rules must
be appended to the single shared bundle:
```
nasiya365/public/css/nasiya365.bundle.css   # append a new banner-commented section, e.g.
                                             # /* ===================== profit_and_loss (page) ===================== */
                                             # using a `.pnl-*` class prefix (parallel to `.bnpl-*`)
```

### E4. Tests — no JS test runner exists in this repo

Confirmed by search: **no `package.json` anywhere in the repo** (only inside `node_modules` of
unrelated tooling, none found at all actually — `find ... -iname package.json` returned nothing),
**no `jest.config.*`, no `*.test.js`/`*.spec.js` files anywhere**. All existing automated tests
are Python, under `nasiya365/tests/` (`test_cost_recovery_engine.py`, `test_data_import.py`,
`test_imei_search.py`, `test_recognition.py`), run via Frappe's/`bench`'s Python test runner
(pytest-based, `unittest.TestCase` subclasses per Frappe convention) — **not** relevant to
front-end JS at all.

**Recommendation (per task spec §17, "написать тесты для adapter/view model" and §15's emphasis
on a pure, Frappe-free adapter): write `pnl_adapter.js` as plain ES5/ES6 JS with no Frappe/browser
globals, and test it with plain `node` directly — no new tooling, no `package.json`, no test
framework dependency.** e.g.:
```
nasiya365/public/js/pnl_adapter.js        # module code, CommonJS-exportable (module.exports = {...} guarded by `typeof module !== "undefined"`) so both the browser (global) and node (CommonJS) can load it, matching how e.g. `recognition.py` (Python) was kept pure/framework-free specifically to be independently unit-testable (`nasiya365/api/recognition.py:1-9` docstring: "No Frappe imports — these helpers are deterministic so they can be unit-tested in isolation") — the same rationale applies to the JS adapter.
```
A standalone test file (location proposed, not required by this brief to create):
```
nasiya365/tests_js/pnl_adapter.test.js    # plain `node nasiya365/tests_js/pnl_adapter.test.js`, asserts via Node's built-in `assert` module — no jest/mocha install needed, can run in CI as an extra `node <file>` step alongside the existing `bench run-tests` Python step.
```
This mirrors the Python precedent (`recognition.py`) of isolating pure business/transform logic
from the framework specifically so it stays independently testable, and avoids introducing a new
JS toolchain (`package.json`, jest, npm install) into a repo/Docker image that currently has none
(the `Dockerfile` has no `npm`/`node` install step for the app image beyond whatever ships inside
the base `frappe/erpnext:v16` image for `bench build`'s own esbuild).

---

## Summary of the most load-bearing findings

1. Custom pages here = `.json` (route/roles) + `.js` (100% of behavior, jQuery + template
   literals, no framework) + boilerplate `.py`. CSS is **not** per-page — everything funnels into
   one shared `nasiya365/public/css/nasiya365.bundle.css`, included site-wide via
   `hooks.py:app_include_css`, because Frappe's esbuild can't resolve per-file `@import`s and the
   image has no node for SCSS compilation.
2. `compute_profit`'s cost-recovery dict has **two sets of same-concept keys**: `sales_*` (Раздел
   1, potential) vs. bare `cash_margin`/`financed_margin`/`interest_income`/`total_margin`
   (Раздел 2, recognized — these bare names are **overwritten in place**, easy to grab the wrong
   one). Table 1 uses `sales_*`; Table 2 uses the bare/recognized ones.
3. "Покрытие себестоимости" and "Заработанная товарная маржа" have **no backend key** — must be
   derived client-side using the exact formula already proven in
   `profit_and_loss_summary.py:15-18` (not `gross_profit`, which is basis-dependent).
4. Today's Report restricts access to `System Manager`/`Nasiya365 Admin`
   (`profit_and_loss_summary.json:21-24`); the two existing example Pages leave `roles: []`
   (open). The new Page must explicitly set the same roles or access silently widens — the
   underlying whitelisted method has no role check of its own, only branch-level row filtering.
5. No JS test runner exists in the repo at all — recommend a pure, Frappe-free `pnl_adapter.js`
   (CommonJS + browser global dual-export) testable via plain `node`, matching the existing Python
   precedent (`nasiya365/api/recognition.py`) of isolating pure logic specifically for
   independent unit testing.
6. Converting Report → Page loses free CSV export / column tooling / workspace `Report` link
   type — all must be re-implemented or the workspace links updated (`nasiya365.json:172,296`
   currently point at `link_type: "Report"`).
