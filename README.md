# Nasiya365 BNPL Platform

A comprehensive Buy Now, Pay Later (BNPL) multi-tenant SaaS platform built on Frappe Framework.

## Features

- **BNPL Core**: Credit scoring, installment plans, payment tracking
- **Branch Management**: Multi-location support with staff roles
- **Warehouse & Inventory**: Stock management across locations
- **Sales**: Cash, BNPL, and mixed payment sales
- **Contract Management**: PDF generation with custom templates

## Requirements

- **Python**: 3.10 or newer (recommended 3.11)
- **Frappe**: 15.0.0 or newer (single required app; no ERPNext)
- **Bench**: Standard Frappe bench (MariaDB, Redis, Node for assets)
- **Optional – PDF generation**: Install [WeasyPrint](https://weasyprint.org/) if you use Print Template to generate contract PDFs:
  ```bash
  pip install -r requirements-optional.txt
  ```
  See [INSTALL_REQUIREMENTS.md](INSTALL_REQUIREMENTS.md) for a full audit of install requirements.

## Installation

```bash
# Get the app (replace with your repo URL)
bench get-app https://github.com/your-org/nasiya365

# Install on site
bench --site your-site.local install-app nasiya365
```

For Docker and production, see [DEPLOYMENT.md](DEPLOYMENT.md) and [.env.prod.example](.env.prod.example).

### Frappe Cloud

Add this app from GitHub: **alraqam/nasiya365-frappe**, branch **main**. The repo uses the standard `pyproject.toml` format Frappe Cloud expects (`[tool.bench.frappe-dependencies]`). If you see "Internal Server Error" when adding the app:

1. Ensure the [Frappe Cloud GitHub App](https://github.com/settings/installations) has access to this repository (or your fork).
2. If the repo path or visibility changed, remove the app in Frappe Cloud and add it again from GitHub.
3. If it still fails, [contact Frappe Cloud support](https://frappecloud.com/support) with your Bench Group name and repo URL.

## License

MIT
