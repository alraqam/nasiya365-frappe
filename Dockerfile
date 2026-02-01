FROM frappe/erpnext:v16

USER root
# Install cron as it is often needed for Frappe
RUN apt-get update && apt-get install -y \
    cron \
    && rm -rf /var/lib/apt/lists/*

# Setup bench home
ENV BENCH_HOME=/home/frappe/frappe-bench
WORKDIR ${BENCH_HOME}

# Remove ERPNext completely (code + pip + registration) BEFORE any bench operations
RUN rm -rf apps/erpnext && \
    ./env/bin/pip uninstall erpnext -y 2>/dev/null || true && \
    sed -i '/erpnext/d' sites/apps.txt && \
    echo "Apps after ERPNext removal:" && cat sites/apps.txt

# Copy custom nasiya365 app and fix ownership
COPY ./nasiya365 apps/nasiya365
RUN chown -R frappe:frappe apps/nasiya365

# Switch to frappe user for bench operations
USER frappe

# Install nasiya365 app and register it
RUN ./env/bin/pip install -e apps/nasiya365 --no-deps && \
    if ! grep -q "nasiya365" sites/apps.txt; then echo "nasiya365" >> sites/apps.txt; fi && \
    echo "Final apps.txt:" && cat sites/apps.txt

# Build assets
RUN bench build --production

# Cache built assets to a separate directory so they survive volume mounting
USER root
RUN mkdir -p /home/frappe/assets_cache && \
    cp -r sites/assets/* /home/frappe/assets_cache/ && \
    chown -R frappe:frappe /home/frappe/assets_cache

USER frappe
# Copy init script
COPY --chown=frappe:frappe init-site.sh /home/frappe/init-site.sh
RUN sed -i 's/\r$//' /home/frappe/init-site.sh && \
    chmod +x /home/frappe/init-site.sh

# Default command
CMD ["sh", "-c", "cd sites && ../env/bin/gunicorn -b 0.0.0.0:8000 -w 4 -t 120 frappe.app:application --preload"]
