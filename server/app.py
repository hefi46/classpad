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
from server.routes.config import bp as config_bp
from server.routes.plugins import bp as plugins_bp


def create_app(data_dir: str | os.PathLike | None = None) -> Flask:
    app = Flask(__name__)

    resolved_data_dir = Path(
        data_dir or os.environ.get("CLASSPAD_DATA_DIR", "server/data")
    )
    resolved_data_dir.mkdir(parents=True, exist_ok=True)
    app.config["DATA_DIR"] = resolved_data_dir

    models.init_db(resolved_data_dir / "classpad.db")
    app.teardown_appcontext(models.close_db)

    app.register_blueprint(config_bp)
    app.register_blueprint(plugins_bp)

    return app


if __name__ == "__main__":
    create_app().run(host="0.0.0.0", port=5000)
