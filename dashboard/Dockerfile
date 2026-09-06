# Build context is the repository root so the shared package can be installed.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

COPY shared /srv/shared
RUN pip install --no-cache-dir /srv/shared

COPY dashboard/requirements.txt /srv/dashboard/requirements.txt
RUN pip install --no-cache-dir -r /srv/dashboard/requirements.txt

COPY dashboard/app /srv/dashboard/app

WORKDIR /srv/dashboard
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
