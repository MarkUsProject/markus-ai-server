FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy project files
COPY pyproject.toml README.md LICENSE ./
COPY src ./src

# Install Python dependencies

RUN pip install --no-cache-dir -e .

# Expose Flask port
EXPOSE 5000

# Run the Flask application
CMD ["python", "-m", "markus_ai_server"]
