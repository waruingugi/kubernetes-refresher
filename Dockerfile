FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir flask==3.0.3 redis==5.0.8
COPY app.py .
EXPOSE 5000
CMD ["python", "app.py"]