FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8
RUN apt-get update && apt-get install -y --no-install-recommends fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY shared shared
COPY sportsman_service sportsman_service
COPY competition_service competition_service
COPY gateway_service gateway_service

ENV FSP_PDF_FONT=/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf \
    FSP_PDF_FONT_BOLD=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf
