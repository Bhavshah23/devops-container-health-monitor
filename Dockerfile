FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY monitor.py .
COPY templates ./templates

RUN useradd --create-home --uid 10001 monitor && \
    mkdir -p /data && chown monitor:monitor /data

EXPOSE 5000

# Docker socket permissions differ between hosts, so Compose starts this lab
# container as root. In production, use a socket proxy and an unprivileged user.
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--threads", "4", "--timeout", "120", "monitor:app"]

