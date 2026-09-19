FROM rust:1.85-slim AS builder
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-pip \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --break-system-packages maturin
COPY core-rs ./core-rs
RUN cd core-rs && maturin build --release --out /wheels

FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir /wheels/*.whl
COPY core ./core
COPY sim ./sim
COPY server ./server

ENV WARDEN_HOST=0.0.0.0
EXPOSE 8765
CMD ["python", "-m", "server.ws_server"]
