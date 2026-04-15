# XAU/USD Trading Bot
# NOTE: MetaTrader5 requires a Windows host with MT5 terminal installed.
# This Dockerfile packages all other dependencies and the bot code.
# Run on a Windows machine with MT5 installed, or use Wine on Linux.

FROM python:3.12-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps (skip MetaTrader5 — platform-specific, installed separately)
COPY requirements.txt .
RUN pip install --no-cache-dir \
    pandas \
    numpy \
    pandas-ta \
    python-dotenv \
    APScheduler \
    requests \
    Flask \
    scikit-learn \
    scipy

# Copy source
COPY *.py .
COPY .env* ./

# Create log directory
RUN mkdir -p logs

# Environment defaults (override via docker-compose or .env)
ENV PYTHONUNBUFFERED=1
ENV LOG_LEVEL=INFO

# Default: run the bot
CMD ["python", "main.py"]
