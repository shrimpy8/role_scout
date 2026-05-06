"""Flask Blueprint for Role Scout Phase 2 dashboard routes."""
from __future__ import annotations

import os
import secrets

import structlog
from flask import Flask, g
from flask_wtf.csrf import CSRFProtect

log = structlog.get_logger()
csrf = CSRFProtect()

_DEV_SECRET_KEY = "dev-insecure-key"


def create_app() -> Flask:
    """Create and configure the Flask application."""
    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
    )

    # C5: Enforce SECRET_KEY in non-dev environments
    secret_key = os.environ.get("FLASK_SECRET_KEY", _DEV_SECRET_KEY)
    if secret_key == _DEV_SECRET_KEY:
        if os.environ.get("LOG_LEVEL", "INFO").upper() != "DEBUG":
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
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "img-src 'self' data:; "
            "connect-src 'self'; "
            "frame-ancestors 'none'"
        )
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    from role_scout.dashboard.routes import bp
    app.register_blueprint(bp)

    return app
