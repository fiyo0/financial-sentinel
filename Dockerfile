# Production Container for Financial Sentinel Multi-Agent System
FROM python:3.11-slim

WORKDIR /app

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY . .

# Ensure storage directories exist and create non-root sentinel user
RUN groupadd -g 1000 sentinel && \
    useradd -u 1000 -g sentinel -m -s /bin/bash sentinel && \
    mkdir -p storage data && \
    chown -R sentinel:sentinel /app

USER sentinel

# Start production server using dynamic Cloud Run PORT
CMD ["sh", "-c", "python -m uvicorn web.app:app --host 0.0.0.0 --port ${PORT:-8080}"]
