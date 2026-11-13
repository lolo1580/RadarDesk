FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore

WORKDIR /app

RUN groupadd --gid 10001 adsb \
    && useradd --uid 10001 --gid adsb --home-dir /nonexistent --no-create-home --shell /usr/sbin/nologin adsb \
    && mkdir -p /stats /tar1090_data \
    && chown adsb:adsb /stats /tar1090_data

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=adsb:adsb server.py ./

USER adsb
EXPOSE 8000

CMD ["python", "server.py"]
