FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
WORKDIR /app
RUN pip install --no-cache-dir flask==3.0.3 redis==5.0.8 psycopg2-binary==2.9.9
COPY app.py .
EXPOSE 5000
CMD ["python", "app.py"]