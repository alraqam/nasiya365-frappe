# Nasiya365 Frappe App – Installation Requirements (Deep Audit)

This document describes all requirements for installing and running the Nasiya365 Frappe app, based on a full review of the project.

---

## 1. Core Python & Frappe Dependencies

| Source | Requirement | Notes |
|--------|-------------|--------|
| **requirements.txt** | `frappe` | Single direct dependency; Frappe brings its own transitive deps. |
| **pyproject.toml** | `frappe` | Same. `[tool.bench]` declares `frappe-dependencies = [">=15.0.0"]`. |
| **setup.py** | Reads `requirements.txt` | `install_requires` come from `requirements.txt`. |
| **hooks.py** | `required_apps = ["frappe"]` | Correct; no other Frappe apps required. |

**Conclusion:** Core dependency declaration is correct and consistent. The app is designed to run on **Frappe 15+** and does not require ERPNext or other apps.

---

## 2. Python Version

| Source | Version |
|--------|---------|
| **.python-version** | `3.11.9` |
| **pyproject.toml** | `requires-python = ">=3.10"` |

Use **Python 3.10 or newer**. The repo pins 3.11.9 for local/CI consistency.

---

## 3. Implicit / Optional Dependencies (Code Usage)

| Package | Where used | In requirements? | Action |
|---------|------------|-------------------|--------|
| **WeasyPrint** | `nasiya365/utils/pdf.py` – contract PDF generation | No | Optional; add to optional deps and document. Code already handles `ImportError`. |
| **requests** | `nasiya365/utils/sms_manager.py` – Eskiz/Playmobile SMS | No (transitive) | Provided by Frappe; no change needed. |
| **jinja2** | `nasiya365/utils/pdf.py` – template rendering | No (transitive) | Provided by Frappe; no change needed. |
| **csv, os, re, json, decimal** | Various doctypes & scripts | Stdlib | No action. |

**WeasyPrint** is required only if you use **Print Template** → “Generate PDF” for contracts. Without it, PDF generation will raise a user-facing error (handled in code). Install explicitly if you need PDFs:  
`pip install weasyprint` or use `requirements-optional.txt` (see below).

---

## 4. Frappe Hooks & Install Flow

- **after_install** (`nasiya365.install.after_install`): Creates default roles and print templates; commits DB.
- **Fixtures**: Custom Field, Property Setter (module = Nasiya365).
- **DocType / scheduler / cron events**: All reference existing modules; no missing imports detected.
- **Jinja filters**: `nasiya365.utils.jinja_filters` (currency_format, date_format) – no extra deps.

Install flow is consistent with a standard Frappe app.

---

## 5. Package Data (MANIFEST.in)

- Includes `requirements.txt`, `*.json`, `*.md`, `*.py`, `*.txt`.
- Recursive includes under `nasiya365` for `.css`, `.csv`, `.html`, `.ico`, `.js`, `.json`, `.md`, `.png`, `.py`, `.svg`, `.txt`.
- Excludes `*.pyc`.

**Note:** First line `include MANIFEST.in` is self-referential and redundant but harmless.

---

## 6. Bench / Site Requirements

- **Bench**: App is intended to be installed with `bench get-app` (or equivalent) and then `bench --site <site> install-app nasiya365`.
- **Database**: MariaDB (or MySQL) – version compatible with Frappe 15+ (e.g. MariaDB 10.6+).
- **Redis**: Used for cache and queue when running under Frappe (e.g. in Docker or production).
- **Node**: Required for building frontend assets (`bench build`); Frappe image / bench already include this.

---

## 7. Docker-Based Installation

| File | Role |
|------|------|
| **Dockerfile** | Base `frappe/erpnext:v16`; removes ERPNext; copies `./nasiya365` → `apps/nasiya365`; `pip install -e apps/nasiya365 --no-deps`; runs `bench build --production`. |
| **docker-compose.yml** / **docker-compose.prod.yml** | MariaDB 10.6, Redis (cache + queue), setup container, backend (Gunicorn), frontend (Nginx), websocket, workers, scheduler. |
| **init-site.sh** | Creates site if missing; runs `bench --site <site> install-app nasiya365` and migrate. |

**Requirements for Docker:**

- Build context must include the `nasiya365` app directory at repo root (same level as Dockerfile).
- Environment variables: see `.env.example` and `.env.prod.example` (DB, Redis, site name, admin password, optional mail, etc.).
- **WeasyPrint**: Not installed in the current Docker image. To enable PDF generation in Docker, extend the Dockerfile with `pip install weasyprint` and any system libs WeasyPrint needs (e.g. on Debian/Ubuntu: `libpango-1.0-0`, `libffi`, etc.).

---

## 8. Environment Variables (Summary)

- **.env.example**: `DB_ROOT_PASSWORD`, `SITE_NAME`, `ADMIN_PASSWORD`, `DNS_MULTITENANT`, optional SSL/site header.
- **.env.prod.example**: Site name, DB host/port/name/user/password, Redis URLs, `ENCRYPTION_KEY`, `ADMIN_PASSWORD`, mail settings, `FRAPPE_SITE_NAME_HEADER`, workers.

Needed for “install” in the sense of configuring the environment that runs the app (especially in Docker/production).

---

## 9. Version Alignment Summary

| Item | Value |
|------|--------|
| App version | 0.0.1 (setup.py; hooks.py) |
| Frappe | ≥ 15.0.0 (pyproject.toml); Docker uses v16 |
| Python | ≥ 3.10 (pyproject); .python-version 3.11.9 |
| MariaDB (Docker) | 10.6 |
| Redis | Image-based (e.g. alpine) |

No conflicts found between declared and runtime versions.

---

## 10. Gaps & Recommendations

1. **WeasyPrint**: Add to optional requirements and document in README (see next section).
2. **README**: Replace placeholder `https://github.com/your-org/nasiya365` with real repo URL when publishing; add a short “Requirements” section (Python 3.10+, Frappe 15+, optional WeasyPrint for PDF).
3. **Root modules.txt**: Contains `Nasiya365` (capital N). Frappe app module in `nasiya365/modules.txt` is `nasiya365` (lowercase). Confirm whether root `modules.txt` is for tooling only; if it’s for Frappe, align with app name (usually lowercase).
4. **patches.txt**: Empty; no migration patches. Fine for now.

---

## 11. Minimal Install (Development)

```bash
# Prerequisites: Python 3.10+, bench, MariaDB, Redis
bench get-app /path/to/nasiya365-frappe   # or git URL
bench --site your-site.local install-app nasiya365
```

Optional (for contract PDFs):

```bash
pip install weasyprint   # or: pip install -r requirements-optional.txt
```

---

## 12. Production / Docker Install

1. Clone repo; ensure `nasiya365` app directory is present.
2. Copy `.env.prod.example` → `.env` (or use your orchestrator’s env).
3. Build and run with `docker-compose.prod.yml` (or your chosen compose file).
4. Ensure DB and Redis are reachable; site name and headers match your domain.
5. If you need PDF generation in containers, add WeasyPrint (and its system dependencies) to the Dockerfile and rebuild.

This completes the deep audit of the Frappe app installing requirements for this project.
