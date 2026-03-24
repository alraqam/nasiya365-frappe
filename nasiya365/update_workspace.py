import json
import os

ws_file = "nasiya365/workspace/nasiya365/nasiya365.json"

with open(ws_file, "r", encoding="utf-8") as f:
    ws = json.load(f)

# The main doctypes that should go in the left sidebar menu (links)
main_doctypes = [
    {"label": "Клиенты", "link_to": "Customer Profile"},
    {"label": "Товары", "link_to": "Product"},
    {"label": "Заказы на продажу", "link_to": "Sales Order"},
    {"label": "Договоры", "link_to": "Contract"},
    {"label": "Планы рассрочки", "link_to": "Installment Plan"},
    {"label": "Платежные транзакции", "link_to": "Payment Transaction"},
    {"label": "Расходы", "link_to": "Expense"}
]

# Other doctypes to also show in the dashboard (shortcuts)
other_doctypes = [
    {"label": "Филиалы", "link_to": "Branch"},
    {"label": "Склады", "link_to": "Warehouse"},
    {"label": "Поставщики", "link_to": "Supplier"},
    {"label": "Складские записи", "link_to": "Stock Entry"},
    {"label": "Складской учет", "link_to": "Stock Ledger"},
    {"label": "Касса", "link_to": "Cashbox"},
    {"label": "Сборщики (Коллекторы)", "link_to": "Collector"},
    {"label": "Категории товаров", "link_to": "Product Category"},
    {"label": "Шаблоны атрибутов", "link_to": "Product Attribute Template"},
    {"label": "Настройки продавца", "link_to": "Merchant Settings"},
    {"label": "Настройки SMS", "link_to": "SMS Gateway Settings"},
    {"label": "Инструмент импорта", "link_to": "Data Import Tool"},
    {"label": "Шаблоны печати", "link_to": "Print Template"}
]

# Build links (Left sidebar)
ws["links"] = []
for doc in main_doctypes:
    ws["links"].append({
        "hidden": 0,
        "is_query_report": 0,
        "label": doc["label"],
        "link_type": "DocType",
        "link_to": doc["link_to"],
        "type": "Link"
    })

# Define colors for variety
colors = ["Blue", "Green", "Orange", "Purple", "Yellow", "Cyan", "Red", "Gray", "Light Blue", "Pink", "Teal"]

# Rebuild shortcuts and content for the Dashboard
ws["shortcuts"] = []
content = [{"id": "heading-nasiya365", "type": "header", "data": {"text": "<span class=\"h4\"><b>Главное</b></span>", "col": 12}}]

all_doctypes = main_doctypes + other_doctypes

for i, doc in enumerate(main_doctypes):
    color = colors[i % len(colors)]
    shortcut_id = f"shortcut-{doc['link_to'].lower().replace(' ', '-')}"
    ws["shortcuts"].append({
        "color": color,
        "doc_view": "List",
        "label": doc["label"],
        "link_to": doc["link_to"],
        "type": "DocType"
    })
    content.append({
        "id": shortcut_id,
        "type": "shortcut",
        "data": {
            "shortcut_name": doc["label"],
            "col": 4
        }
    })

content.append({"id": "heading-other", "type": "header", "data": {"text": "<span class=\"h4\"><b>Справочники и Настройки</b></span>", "col": 12}})

for i, doc in enumerate(other_doctypes):
    color = "Gray"
    shortcut_id = f"shortcut-{doc['link_to'].lower().replace(' ', '-')}"
    ws["shortcuts"].append({
        "color": color,
        "doc_view": "List",
        "label": doc["label"],
        "link_to": doc["link_to"],
        "type": "DocType"
    })
    content.append({
        "id": shortcut_id,
        "type": "shortcut",
        "data": {
            "shortcut_name": doc["label"],
            "col": 4
        }
    })

import json
ws["content"] = json.dumps(content)

with open(ws_file, "w", encoding="utf-8") as f:
    json.dump(ws, f, ensure_ascii=False, indent=4)
