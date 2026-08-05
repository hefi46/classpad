#!/usr/bin/env bash
# Quickstart for the classpad admin server: creates server/.env if it's
# missing (prompting for the admin credentials required by app.py, see
# CLASSPAD_ADMIN_USERNAME/CLASSPAD_ADMIN_PASSWORD/CLASSPAD_SECRET_KEY in
# server/docker-compose.yml — there are no defaults on purpose), then runs
# `docker compose up --build -d`.
#
# Safe to re-run: if server/.env already exists it's left untouched and
# this just (re)starts the container.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

ENV_FILE="$SCRIPT_DIR/.env"

if [ -f "$ENV_FILE" ]; then
    echo "Found existing $ENV_FILE — leaving it as-is."
elif [ -n "${CLASSPAD_ADMIN_USERNAME:-}" ] && [ -n "${CLASSPAD_ADMIN_PASSWORD:-}" ] && [ -n "${CLASSPAD_SECRET_KEY:-}" ]; then
    echo "CLASSPAD_ADMIN_USERNAME/PASSWORD/SECRET_KEY are already set in the shell — skipping .env creation."
else
    echo "No server/.env found — let's create one."
    echo "(This file is gitignored and stays local to this machine — never commit it.)"
    echo

    read -rp "Admin username [admin]: " admin_username
    admin_username="${admin_username:-admin}"

    while true; do
        read -rsp "Admin password: " admin_password
        echo
        if [ -n "$admin_password" ]; then
            break
        fi
        echo "Password can't be empty."
    done

    # CLASSPAD_SECRET_KEY signs Flask's session cookie — a random value is
    # fine, nobody needs to remember it. Prefer openssl, fall back to
    # python3 (both are already required elsewhere in this repo).
    if command -v openssl >/dev/null 2>&1; then
        secret_key="$(openssl rand -hex 32)"
    else
        secret_key="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
    fi

    cat > "$ENV_FILE" <<EOF
CLASSPAD_ADMIN_USERNAME=$admin_username
CLASSPAD_ADMIN_PASSWORD=$admin_password
CLASSPAD_SECRET_KEY=$secret_key
EOF
    chmod 600 "$ENV_FILE"
    echo "Wrote $ENV_FILE (chmod 600, admin-readable only)."
fi

echo
echo "Starting the classpad server (docker compose up --build -d)..."
echo

if ! command -v docker >/dev/null 2>&1; then
    echo "docker not found — install Docker before running this script." >&2
    exit 1
fi

if docker compose version >/dev/null 2>&1; then
    compose=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
    compose=(docker-compose)
else
    echo "Neither 'docker compose' nor 'docker-compose' is available — install Docker Compose." >&2
    exit 1
fi

if ! "${compose[@]}" up --build -d; then
    status=$?
    echo
    echo "docker compose failed (exit $status)." >&2
    if ! docker info >/dev/null 2>&1; then
        echo "Looks like the current user can't talk to the Docker daemon." >&2
        echo "Either re-run this script with sudo, or add yourself to the docker group and log back in:" >&2
        echo "    sudo usermod -aG docker \$USER" >&2
    fi
    exit "$status"
fi

echo
echo "Server is starting. Once it's up:"
echo "  Admin portal: http://$(hostname):5000/admin"
echo "  Logs:         docker compose logs -f"
