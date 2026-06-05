FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy all code
COPY entrypoint.py .
COPY processor/ processor/
COPY api/ api/

# Default mode is serve
ENV RUN_MODE=serve

EXPOSE 8000

CMD ["python", "entrypoint.py"]