# syntax=docker/dockerfile:1.7

ARG PYTHON_IMAGE=python:3.13-slim-bookworm
FROM ${PYTHON_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv/control-center

RUN groupadd --gid 10001 controlcenter \
    && useradd --uid 10001 --gid 10001 --no-create-home \
        --home-dir /nonexistent --shell /usr/sbin/nologin controlcenter

# The web Control Center does not need the Windows desktop dependencies
# (pywebview/pythonnet). Install only its direct runtime set while using the
# existing lock as constraints for every transitive dependency.
RUN --mount=type=bind,source=requirements/windows-runtime.lock,target=/tmp/runtime.lock,readonly \
    python -m pip install --no-cache-dir --no-compile \
        --constraint /tmp/runtime.lock \
        fastapi \
        Jinja2 \
        itsdangerous \
        python-multipart \
        SQLAlchemy \
        tzdata \
        uvicorn

COPY --chown=0:0 app ./app
COPY --chown=0:0 control_center ./control_center
COPY --chown=0:0 sanitization_contract.py ./sanitization_contract.py

RUN python -m compileall -q app control_center sanitization_contract.py \
    && find /srv/control-center -type d -exec chmod 0555 {} + \
    && find /srv/control-center -type f -exec chmod 0444 {} +

USER 10001:10001

EXPOSE 10000

CMD ["/bin/sh", "-c", "exec uvicorn control_center.web:create_control_center_app --factory --host 0.0.0.0 --port \"${PORT:-10000}\" --proxy-headers --forwarded-allow-ips \"${CONTROL_CENTER_FORWARDED_ALLOW_IPS:-127.0.0.1}\" --no-server-header --no-access-log"]
