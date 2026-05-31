FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install dependencies first (Docker layer caching — faster rebuilds)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app code
COPY main.py .
COPY geocoder.py .
COPY data_loader.py .

# Expose port
EXPOSE 8000

# Run
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]