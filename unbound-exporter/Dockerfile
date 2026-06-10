FROM python:3.14-slim

WORKDIR /app

RUN pip install --no-cache-dir prometheus_client==0.21.1 requests==2.32.3

COPY unbound_exporter.py .

EXPOSE 9101

CMD ["python", "unbound_exporter.py"]
