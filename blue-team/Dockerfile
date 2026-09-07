# Build context is the repository root so the shared package can be installed.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

COPY shared /srv/shared
RUN pip install --no-cache-dir /srv/shared

COPY blue-team/requirements.txt /srv/blue-team/requirements.txt
RUN pip install --no-cache-dir -r /srv/blue-team/requirements.txt

COPY blue-team/app /srv/blue-team/app

WORKDIR /srv/blue-team
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
