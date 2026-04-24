"""Flask Blueprint for Role Scout Phase 2 dashboard routes."""
from __future__ import annotations

from flask import Flask
from flask_wtf.csrf import CSRFProtect

csrf = CSRFProtect()


def create_app() -> Flask:
    """Create and configure the Flask application."""
    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
    )
    # SECRET_KEY required for CSRF; loaded from env in production
    import os
    app.config["SECRET_KEY"] = os.environ.get("FLASK_SECRET_KEY", "dev-insecure-key")
    app.config["WTF_CSRF_CHECK_DEFAULT"] = True

    csrf.init_app(app)

    @app.after_request
    def _set_security_headers(response):
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
