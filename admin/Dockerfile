FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore

WORKDIR /app

RUN groupadd --gid 10001 radardesk \
    && useradd --uid 10001 --gid radardesk --home-dir /nonexistent \
       --no-create-home --shell /usr/sbin/nologin radardesk \
    && mkdir -p /data \
    && chown radardesk:radardesk /data

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=radardesk:radardesk server.py ./
COPY --chown=radardesk:radardesk static ./static/

# Le volume /data est préparé par admin_data_init (uid/gid 10001).
USER 10001:10001
EXPOSE 8000

CMD ["python", "server.py"]
