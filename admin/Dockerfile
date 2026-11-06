FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py ./
COPY static ./static/
RUN mkdir -p /data

# Le service tourne en root uniquement pour écrire le volume partagé /data.
# Il reste protégé par Basic Auth et n'est jamais exposé au backend public.
EXPOSE 8000

CMD ["python", "server.py"]
