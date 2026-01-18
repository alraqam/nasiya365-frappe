# Nasiya365 Production Dockerfile
FROM frappe/erpnext:v16

# Copy custom app
COPY ./nasiya365 /home/frappe/frappe-bench/apps/nasiya365

# Install custom app
RUN cd /home/frappe/frappe-bench && \
    ./env/bin/pip install -e apps/nasiya365 --no-deps

# Set working directory
WORKDIR /home/frappe/frappe-bench

# Expose port
EXPOSE 8000

# Default command (will be overridden by docker-compose)
CMD ["sh", "-c", "cd sites && ../env/bin/gunicorn -b 0.0.0.0:8000 -w 4 -t 120 frappe.app:application --preload"]
