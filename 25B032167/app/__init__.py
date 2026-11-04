"""Flask application factory for the KazBank Transaction API."""
from datetime import date, datetime
from decimal import Decimal

from dotenv import load_dotenv
from flask import Flask
from flask.json.provider import DefaultJSONProvider

from . import db
from .errors import register_error_handlers


class JSONProvider(DefaultJSONProvider):
    """Decimal -> string (no float rounding for money), datetime -> ISO 8601."""

    @staticmethod
    def default(o):
        if isinstance(o, Decimal):
            return str(o)
        if isinstance(o, (datetime, date)):
            return o.isoformat()
        return DefaultJSONProvider.default(o)


def create_app(database_url: str | None = None) -> Flask:
    load_dotenv()
    app = Flask(__name__)
    app.json = JSONProvider(app)
    app.json.sort_keys = False

    db.init_pool(database_url)          # pool created once, at startup
    register_error_handlers(app)

    from .routes import bp
    app.register_blueprint(bp)
    return app
