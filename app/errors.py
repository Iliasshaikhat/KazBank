"""Custom application errors + mapping of psycopg3 exceptions to HTTP codes."""
from flask import jsonify
from psycopg import OperationalError
from psycopg import errors as pg
from psycopg_pool import PoolTimeout


class ApiError(Exception):
    """Business-rule error that already knows its HTTP status."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


def _json_error(message: str, status: int):
    return jsonify({"error": message}), status


def register_error_handlers(app):
    @app.errorhandler(ApiError)
    def handle_api_error(e: ApiError):
        return _json_error(e.message, e.status)

    # --- specific psycopg3 exceptions required by the assignment ---------
    @app.errorhandler(pg.UniqueViolation)
    def handle_unique(e):
        return _json_error("Duplicate value: resource already exists", 409)

    @app.errorhandler(pg.NotNullViolation)
    def handle_not_null(e):
        col = e.diag.column_name or "unknown"
        return _json_error(f"Missing required field: {col}", 400)

    @app.errorhandler(OperationalError)
    def handle_operational(e):
        return _json_error("Database unavailable", 503)

    # --- other realistic DB failures --------------------------------------
    @app.errorhandler(PoolTimeout)
    def handle_pool_timeout(e):
        return _json_error("Database busy, try again later", 503)

    @app.errorhandler(pg.ForeignKeyViolation)
    def handle_fk(e):
        return _json_error("Referenced resource does not exist", 404)

    @app.errorhandler(pg.CheckViolation)
    def handle_check(e):
        return _json_error("Value violates a database constraint", 422)

    @app.errorhandler(pg.InvalidTextRepresentation)
    @app.errorhandler(pg.NumericValueOutOfRange)
    def handle_bad_value(e):
        return _json_error("Invalid value type", 400)

    @app.errorhandler(404)
    def handle_404(e):
        return _json_error("Not found", 404)

    @app.errorhandler(405)
    def handle_405(e):
        return _json_error("Method not allowed", 405)
