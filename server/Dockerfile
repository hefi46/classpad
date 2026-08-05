FROM python:3.12-slim

WORKDIR /app

COPY server/requirements.txt server/requirements.txt
RUN pip install --no-cache-dir -r server/requirements.txt

COPY server/ server/
# The admin portal's plugin-upload validation reuses the manifest schema
# from launcher/plugin_manager.py rather than re-implementing it — one
# schema, one validator, shared by the client's local scan and the
# server's upload path. plugin_manager.py imports pygame at module level
# (for validate_manifest()'s icon decode-check, added 2026-08-03), so
# pygame is in server/requirements.txt too — see the comment there.
# server/seed_plugins.py (below) relies on the same import chain.
COPY launcher/ launcher/
# Read by server/seed_plugins.py to build the initial catalogue on a fresh
# install — see that file for what's actually seeded and why this isn't
# automatic inside create_app()/init_db() itself.
COPY plugins/ plugins/

ENV CLASSPAD_DATA_DIR=/data
EXPOSE 5000

# seed_plugins.py is idempotent (no-ops once the plugins table has any
# rows), so running it on every container start is safe — not just the
# very first one. `exec` hands off to gunicorn as PID 1 so it still
# receives `docker stop`'s SIGTERM directly rather than the shell eating it.
CMD ["sh", "-c", "python -m server.seed_plugins && exec gunicorn --bind 0.0.0.0:5000 server.app:create_app()"]
