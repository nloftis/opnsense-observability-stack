FROM python:3.14-slim

WORKDIR /app

RUN pip install --no-cache-dir prometheus_client==0.26.0 requests==2.34.2

COPY unbound_exporter.py .

EXPOSE 9101

# Exporter requires no root privileges.
USER 65534:65534

CMD ["python", "unbound_exporter.py"]
