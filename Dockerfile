# Text2Hand, served by gunicorn on port 8000.
#
#   docker build -t text2hand .
#   docker run -p 8000:8000 text2hand
#
# One web process, several threads: jobs live in that process's memory (see
# wsgi.py). It starts T2H_RENDER_PROCS render workers (2 by default), each of
# which can peak near 1 GB on a long document, so give the container 3 GB, or
# set T2H_RENDER_PROCS=1 to fit in 2 GB.
FROM python:3.12-slim

# Tesseract for the local OCR converter, which reads scanned PDFs
RUN apt-get update \
 && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock gunicorn==23.0.0

COPY . .

# never run a public server as root
RUN useradd --create-home --uid 10001 t2h \
 && mkdir -p /data && chown t2h /data
USER t2h

ENV T2H_RENDER_ROOT=/data \
    PYTHONUNBUFFERED=1

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4)"

CMD ["gunicorn", "wsgi:app", "--workers", "1", "--threads", "8", "--timeout", "180", \
     "--bind", "0.0.0.0:8000", "--access-logfile", "-"]
