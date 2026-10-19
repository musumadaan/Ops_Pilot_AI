FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    libglib2.0-0 \
    libgl1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY . .

RUN python -m pip install --no-cache-dir -e ".[demo]"

CMD ["sh", "-c", "python -m streamlit run app/streamlit_app.py --server.address 0.0.0.0 --server.port ${PORT:-8501}"]
