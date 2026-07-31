FROM python:3.12-slim

WORKDIR /app

COPY server/requirements.txt server/requirements.txt
RUN pip install --no-cache-dir -r server/requirements.txt

COPY server/ server/
# The admin portal's plugin-upload validation reuses the manifest schema
# from launcher/plugin_manager.py (pure stdlib, no pygame import at module
# level) rather than re-implementing it — one schema, one validator, shared
# by the client's local scan and the server's upload path.
COPY launcher/ launcher/

ENV CLASSPAD_DATA_DIR=/data
EXPOSE 5000

CMD ["gunicorn", "--bind", "0.0.0.0:5000", "server.app:create_app()"]
