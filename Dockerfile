FROM python:3.13-slim

ARG APP_UID=1000
ARG APP_GID=1000

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    IMAGE_UPLOAD_DIR=/image_upload_dir \
    APP_DATA_DIR=/app/data

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir --requirement requirements.txt

COPY app ./app

RUN groupadd --gid ${APP_GID} streetview \
    && useradd --uid ${APP_UID} --gid streetview --no-create-home streetview \
    && mkdir -p /app/data /image_upload_dir \
    && chown -R streetview:streetview /app /image_upload_dir

USER streetview

VOLUME ["/app/data", "/image_upload_dir"]

CMD ["python", "-m", "app"]
