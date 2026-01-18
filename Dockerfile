# Dockerfile for Analytics Database Client App
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    STREAMLIT_SERVER_PORT=8501 \
    STREAMLIT_SERVER_ADDRESS=0.0.0.0 \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

# Install system dependencies
RUN apt-get update && apt-get install -y \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements_app.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements_app.txt

# Copy application code
COPY . .

# Create .streamlit directory if it doesn't exist
RUN mkdir -p .streamlit

# Expose Streamlit port
EXPOSE 8501

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:8501/_stcore/health')" || exit 1

# Run Streamlit app
CMD ["streamlit", "run", "src/ui_streamlit/app.py", "--server.port=8501", "--server.address=0.0.0.0"]

