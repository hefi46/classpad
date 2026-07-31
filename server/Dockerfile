FROM python:3.12-slim

WORKDIR /app

COPY server/requirements.txt server/requirements.txt
RUN pip install --no-cache-dir -r server/requirements.txt

COPY server/ server/

ENV CLASSPAD_DATA_DIR=/data
EXPOSE 5000

CMD ["gunicorn", "--bind", "0.0.0.0:5000", "server.app:create_app()"]
