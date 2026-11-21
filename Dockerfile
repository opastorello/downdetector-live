# Imagem única para os serviços Python (exporter, notifier, api).
# Sem dependências externas — só a stdlib do Python.
FROM python:3.12-slim

WORKDIR /app
COPY server/ ./server/
COPY exporter/ ./exporter/
COPY alerts/ ./alerts/
COPY examples/ ./examples/

# padrão: exporter Prometheus. Sobrescreva o command no compose para notifier/api.
EXPOSE 9693 8000
CMD ["python3", "exporter/downdetector_exporter.py"]
