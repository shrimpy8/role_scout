"""Flask Blueprint for Role Scout Phase 2 dashboard routes."""
from __future__ import annotations

import secrets

import structlog
from flask import Flask, g
from flask_wtf.csrf import CSRFProtect

log = structlog.get_logger()
csrf = CSRFProtect()

_DEV_SECRET_KEY = "dev-insecure-key"  # checked in guard below — change both together


def create_app(flask_secret_key: str | None = None, log_level: str | None = None) -> Flask:
    """Create and configure the Flask application."""
    from role_scout.config import get_settings
    _settings = get_settings()
    if flask_secret_key is None:
        flask_secret_key = _settings.FLASK_SECRET_KEY
    if log_level is None:
        log_level = _settings.LOG_LEVEL

    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
    )

    # C5: Enforce SECRET_KEY in non-dev environments
    secret_key = flask_secret_key or _DEV_SECRET_KEY
    if secret_key == _DEV_SECRET_KEY:
        if log_level.upper() != "DEBUG":
            raise RuntimeError(
                "FLASK_SECRET_KEY is not set. "
                "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\" "
                "and add it to your .env file. Set LOG_LEVEL=DEBUG to allow the insecure default in dev."
            )
        log.warning("flask_secret_key_insecure", hint="Set FLASK_SECRET_KEY in .env for production")
    app.config["SECRET_KEY"] = secret_key
    app.config["WTF_CSRF_CHECK_DEFAULT"] = True

    csrf.init_app(app)

    # H1: Generate request_id per request for traceability
    @app.before_request
    def _assign_request_id() -> None:
        g.request_id = "req_" + secrets.token_hex(8)
        structlog.contextvars.bind_contextvars(request_id=g.request_id)

    @app.after_request
    def _set_security_headers(response):
        response.headers["X-Request-Id"] = getattr(g, "request_id", "")
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "img-src 'self' data:; "
            "connect-src 'self'; "
            "frame-ancestors 'none'"
        )
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    # Store settings once per app lifetime so route handlers don't re-read .env on every request
    app.config["RS_SETTINGS"] = _settings
    # Expose feature flags to all Jinja templates
    app.jinja_env.globals["manual_ingest_enabled"] = _settings.MANUAL_INGEST_ENABLED

    # Run DB migrations on every startup so the dashboard works standalone (--serve only).
    # Wrapped in try/except: test fixtures use a minimal schema that lacks some indexes;
    # production DBs have the full schema and this always succeeds.
    try:
        from role_scout.db import init_db as _init_db
        _init_db(_settings.DB_PATH)
    except Exception:
        log.debug("db_startup_migration_skipped", hint="minimal schema (test) or already current")

    from role_scout.dashboard.routes import bp
    app.register_blueprint(bp)

    return app
