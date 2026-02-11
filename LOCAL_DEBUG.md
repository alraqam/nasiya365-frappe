# Local deployment – Internal Server Error

If you see **Internal Server Error** at `localhost:8080` (or your bench port):

## 1. Get the real error

**Option A – Terminal (recommended)**  
From your bench root:
```bash
bench start
```
Reproduce the error in the browser. The **traceback** will appear in the terminal.

**Option B – Log file**  
Check:
- `sites/<your-site>/logs/web.error.log`
- or `logs/<your-site>.error.log`

**Option C – Console**  
```bash
bench --site <your-site> console
```
Then in Python:
```python
import nasiya365
import nasiya365.nasiya365.doctype.sales_order.sales_order
# If any line raises, that’s the failing import
```

## 2. Common fixes after restructure

**Clear cache**
```bash
bench --site <your-site> clear-cache
```

**Reinstall app (if DocTypes/workspace are missing)**
```bash
bench --site <your-site> reinstall-app nasiya365
# or
bench --site <your-site> migrate
```

**Ensure app is in the bench**
```bash
cd /path/to/your-bench
bench get-app /path/to/nasiya365-frappe
bench --site <your-site> install-app nasiya365
```

## 3. Desktop / `get_data`

If the error is **`get_data() takes exactly 1 argument (0 given)`** (or similar), it’s already handled: `config/desktop.py` uses `def get_data(user=None)`. If you still see it, pull the latest and clear cache.

## 5. Docker: assets 404 (hash mismatch)

If you see **404** for `/assets/frappe/dist/css/*.bundle.*.css` (or JS), the frontend and backend must use the **same** asset hashes. In this project:

- **Assets are built once** in the Docker image and copied to a cache. On startup, the **setup** container copies that cache into the shared **assets** volume and flushes Redis so the backend does not serve stale hashes.
- **Do not run `bench build`** inside the backend at runtime (e.g. via `docker-compose exec backend bench build`). That creates new hashes in the volume while the backend may still use old ones from cache, causing 404s.
- To reset to a clean state: `docker-compose down -v`, then `docker-compose up -d`. Recreate the site and install the app if needed (see init-site.sh or deploy docs).

## 6. Still failing?

Share the **full traceback** from `bench start` or the error log so we can target the exact line.
