# ─────────────────────────────────────────────────────────────────────────────
# Genmars Business Platform — the API image.
#
# Two stages. The compiler and headers that argon2-cffi and psycopg need live
# in the build stage and never reach production: a toolchain in a running
# container is a convenience for an attacker and for nobody else.
#
# ── WHY 3.14 HERE WHEN gen-portal PINS 3.13 ─────────────────────────────────
#
# gen-portal is held at 3.13 because DRF 3.16 does not claim 3.14 in its
# classifiers, and the pair has to be one somebody upstream also runs. This
# project is on Django 6.1 and DRF 3.18, and BOTH list 3.14. Checked, not
# assumed.
# ─────────────────────────────────────────────────────────────────────────────

# ---- build ------------------------------------------------------------------

FROM python:3.14-slim AS build

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends build-essential libpq-dev; \
    rm -rf /var/lib/apt/lists/*

# A virtualenv rather than system site-packages, so the runtime stage takes the
# whole thing as one directory with one COPY.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# ---- runtime ----------------------------------------------------------------

FROM python:3.14-slim AS runtime

LABEL org.opencontainers.image.title="business-platform-api" \
      org.opencontainers.image.vendor="Genmars Tech Limited" \
      org.opencontainers.image.source="https://github.com/genmarstech/business-os"

# DJANGO_SETTINGS_MODULE is set HERE rather than left to wsgi.py.
#
# gunicorn works either way, because wsgi.py sets it on import. Nothing else
# does: `docker compose exec api python -c "import django; django.setup()"`
# — the way anybody inspects this container during an incident — dies with
# ImproperlyConfigured, and so does every `manage.py` invocation that does not
# go through the script's own default. An environment that only the web server
# has is an environment that fails at the moment somebody needs a shell.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    DJANGO_SETTINGS_MODULE=Business_Platform.settings

# libpq5 is the RUNTIME half of libpq-dev — psycopg needs the shared library,
# not the headers. Installing libpq-dev here would drag the compiler back in.
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends libpq5 curl; \
    rm -rf /var/lib/apt/lists/*; \
    groupadd -g 10004 -r app; \
    useradd -u 10004 -r -g app -s /usr/sbin/nologin -d /app app

WORKDIR /app

COPY --from=build /opt/venv /opt/venv
COPY --chown=10004:10004 . .

# collectstatic at BUILD time, not at boot. An entrypoint doing it would mean
# every restart races to write the same files, and a read-only container would
# fail to start for a reason with nothing to do with serving requests.
#
# The build has no database and no real secret, so both are stubbed for this
# one command. Nothing is baked in: settings read the environment at runtime.
RUN set -eux; \
    DEBUG=True \
    DJANGO_SECRET_KEY=build-time-only-not-a-secret \
    python manage.py collectstatic --noinput --clear; \
    rm -f db.sqlite3; \
    chown -R 10004:10004 /app/staticfiles

USER 10004:10004

EXPOSE 8020

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8020/healthz || exit 1

# gunicorn, not `manage.py runserver`. runserver is single-threaded, reloads on
# file changes and says itself that it is not for production.
#
# --access-logfile - sends logs to stdout so the container log driver handles
# rotation; a file inside the container would grow until the disk filled.
#
# Three workers because a till that waits is a queue that waits. Raise it when
# there is evidence, not before.
CMD ["gunicorn", "Business_Platform.wsgi:application", \
     "--bind", "0.0.0.0:8020", \
     "--workers", "3", \
     "--timeout", "30", \
     "--graceful-timeout", "30", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "--forwarded-allow-ips", "*"]
