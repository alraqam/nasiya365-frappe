# Nasiya365 Frappe

## Описание
Система управления рассрочкой (BNPL) на базе Frappe Framework v16.

## Функционал
- Управление клиентами
- Каталог товаров с категориями
- Заказы и договоры
- Планы рассрочки
- Платежи и транзакции
- Складской учёт
- Касса
- Коллекторы

## Установка

### Docker (рекомендуется)
```bash
docker-compose -f docker-compose.prod.yml up -d
```

### Создание сайта
```bash
docker compose exec backend bench new-site your-domain.com
docker compose exec backend bench --site your-domain.com install-app nasiya365
docker compose exec backend bench --site your-domain.com migrate
```

## Лицензия
MIT
