# Playwright Service Dockerfile
# Uses official Playwright Python image with browsers pre-installed
# v1.50.0-noble uses Ubuntu 24.04 LTS (Noble) with new headless mode

FROM mcr.microsoft.com/playwright/python:v1.50.0-noble

WORKDIR /app

# Install Xvfb for headed mode on Linux servers (virtual display)
# Still needed as fallback for login if new headless mode doesn't bypass Xero's anti-bot
RUN apt-get update && apt-get install -y xvfb x11-utils curl && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app/ ./app/
COPY scripts/ ./scripts/
COPY run.py ./run.py
COPY start.sh ./start.sh

# Fix line endings (Windows -> Unix) and make startup script executable
RUN sed -i 's/\r$//' /app/start.sh && chmod +x /app/start.sh

# Create directories for downloads, screenshots, and sessions
RUN mkdir -p /app/downloads /app/screenshots /app/sessions

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
# Default display for Xvfb (will be set by start.sh, but good to have as fallback)
ENV DISPLAY=:99

# Expose the FastAPI port
EXPOSE 8000

# Health check - increased start period to allow for Xvfb startup and slow DB connections
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8000/api/health || exit 1

# Run with startup script that initializes Xvfb for headed browser mode
CMD ["/app/start.sh"]
