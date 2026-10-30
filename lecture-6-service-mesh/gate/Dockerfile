FROM alpine/helm:3.19.0 AS helm
FROM python:3.13-alpine
COPY --from=helm /usr/bin/helm /usr/local/bin/helm
COPY gate.py /app/gate.py
WORKDIR /work
ENTRYPOINT ["python3", "/app/gate.py"]
