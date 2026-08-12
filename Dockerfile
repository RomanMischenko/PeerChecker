FROM python:3.11-slim

WORKDIR /app

# Prevent Python from writing .pyc files to disk and enable bufferless output
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY . .

# Ensure data directory exists
RUN mkdir -p /app/data

# Run application
CMD ["python", "-m", "app.main"]
