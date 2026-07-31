"""Flask app factory.

No module-level `app = create_app()` — that would run `init_db()` (and
create a default `server/data/` directory) as a side effect of merely
importing this module, including during test collection. Gunicorn is
pointed at the factory itself (`server.app:create_app()`, see
server/Dockerfile) rather than an eagerly-created instance.
"""

import os
from pathlib import Path

from flask import Flask

from server import models
from server.routes.admin import bp as admin_bp
from server.routes.config import bp as config_bp
from server.routes.plugins import bp as plugins_bp


def create_app(
    data_dir: str | os.PathLike | None = None,
    *,
    admin_username: str | None = None,
    admin_password: str | None = None,
    secret_key: str | None = None,
) -> Flask:
    app = Flask(__name__)

    resolved_data_dir = Path(
        data_dir or os.environ.get("CLASSPAD_DATA_DIR", "server/data")
    )
    resolved_data_dir.mkdir(parents=True, exist_ok=True)
    app.config["DATA_DIR"] = resolved_data_dir

    admin_username = admin_username or os.environ.get("CLASSPAD_ADMIN_USERNAME")
    admin_password = admin_password or os.environ.get("CLASSPAD_ADMIN_PASSWORD")
    secret_key = secret_key or os.environ.get("CLASSPAD_SECRET_KEY")
    if not admin_username or not admin_password or not secret_key:
        raise RuntimeError(
            "CLASSPAD_ADMIN_USERNAME, CLASSPAD_ADMIN_PASSWORD, and "
            "CLASSPAD_SECRET_KEY must all be set (env vars, or "
            "admin_username=/admin_password=/secret_key= for tests) — the "
            "admin portal has no default credentials."
        )
    app.config["ADMIN_USERNAME"] = admin_username
    app.config["ADMIN_PASSWORD"] = admin_password
    app.config["SECRET_KEY"] = secret_key
    app.config["MAX_CONTENT_LENGTH"] = 64 * 1024 * 1024  # generous for a plugin zip

    models.init_db(resolved_data_dir / "classpad.db")
    app.teardown_appcontext(models.close_db)

    app.register_blueprint(config_bp)
    app.register_blueprint(plugins_bp)
    app.register_blueprint(admin_bp)

    return app


if __name__ == "__main__":
    create_app().run(host="0.0.0.0", port=5000)
