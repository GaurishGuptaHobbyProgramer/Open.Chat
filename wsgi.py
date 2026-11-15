from app import _verify_postgresql_schema, app


if app.config["APP_ENV"] in {"production", "prod"}:
    _verify_postgresql_schema(app)
