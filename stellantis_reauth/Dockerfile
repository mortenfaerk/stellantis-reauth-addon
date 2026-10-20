FROM mcr.microsoft.com/playwright/python:v1.62.0-noble

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --break-system-packages -r requirements.txt

COPY app/ /app/

CMD ["python3", "-u", "/app/main.py"]
