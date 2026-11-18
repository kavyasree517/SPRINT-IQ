from flask import Flask, jsonify, request
from flask_login import LoginManager
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFError, CSRFProtect
from werkzeug.exceptions import HTTPException
from sqlalchemy import event, inspect

# Initialize extensions
db = SQLAlchemy()
csrf = CSRFProtect()
login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message = "Please sign in to access this page."
login_manager.login_message_category = "warning"


@login_manager.user_loader
def load_user(user_id):
    from .models import User

    try:
        return db.session.get(User, int(user_id))
    except (TypeError, ValueError):
        return None


def _enable_sqlite_foreign_keys(connection, _record):
    connection.execute("PRAGMA foreign_keys=ON")


def _upgrade_legacy_users_table():
    inspector = inspect(db.engine)
    if db.engine.dialect.name != "sqlite" or "users" not in inspector.get_table_names():
        return

    columns = {column["name"] for column in inspector.get_columns("users")}
    with db.engine.begin() as connection:
        if "role" not in columns:
            connection.exec_driver_sql(
                "ALTER TABLE users ADD COLUMN role VARCHAR(32) "
                "NOT NULL DEFAULT 'team_member'"
            )
        if "created_at" not in columns:
            connection.exec_driver_sql(
                "ALTER TABLE users ADD COLUMN created_at DATETIME NOT NULL "
                "DEFAULT '1970-01-01 00:00:00'"
            )
        connection.exec_driver_sql(
            "UPDATE users SET role = 'team_member' WHERE role IS NULL"
        )
        connection.exec_driver_sql(
            "UPDATE users SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL"
        )


def _upgrade_phase3_schema():
    inspector = inspect(db.engine)
    if db.engine.dialect.name != "sqlite":
        return

    tables = set(inspector.get_table_names())
    with db.engine.begin() as connection:
        if "projects" in tables:
            project_columns = {
                column["name"] for column in inspector.get_columns("projects")
            }
            if "owner_id" not in project_columns:
                connection.exec_driver_sql(
                    "ALTER TABLE projects ADD COLUMN owner_id INTEGER "
                    "REFERENCES users(id) ON DELETE SET NULL"
                )
            if "ix_projects_owner_id" not in {
                index["name"] for index in inspector.get_indexes("projects")
            }:
                connection.exec_driver_sql(
                    "CREATE INDEX ix_projects_owner_id ON projects (owner_id)"
                )

        if "team_members" in tables:
            member_columns = {
                column["name"] for column in inspector.get_columns("team_members")
            }
            if "email" not in member_columns:
                connection.exec_driver_sql(
                    "ALTER TABLE team_members ADD COLUMN email VARCHAR(120)"
                )
            unique_email_exists = any(
                constraint.get("column_names") == ["email"]
                for constraint in inspector.get_unique_constraints("team_members")
            ) or any(
                index.get("unique")
                and index.get("column_names") == ["email"]
                for index in inspector.get_indexes("team_members")
            )
            if not unique_email_exists:
                connection.exec_driver_sql(
                    "CREATE UNIQUE INDEX uq_team_members_email "
                    "ON team_members (email)"
                )


def _upgrade_phase4_schema():
    inspector = inspect(db.engine)
    if db.engine.dialect.name != "sqlite" or "tasks" not in inspector.get_table_names():
        return

    columns = {column["name"] for column in inspector.get_columns("tasks")}
    if "required_skills" not in columns:
        with db.engine.begin() as connection:
            connection.exec_driver_sql(
                "ALTER TABLE tasks ADD COLUMN required_skills TEXT "
                "NOT NULL DEFAULT ''"
            )


def _upgrade_phase7_schema():
    inspector = inspect(db.engine)
    if "tasks" not in inspector.get_table_names():
        return

    columns = {column["name"] for column in inspector.get_columns("tasks")}
    if "actual_hours" not in columns:
        with db.engine.begin() as connection:
            connection.exec_driver_sql(
                "ALTER TABLE tasks ADD COLUMN actual_hours FLOAT NOT NULL DEFAULT 0"
            )


def create_app(test_config=None):
    """Application factory.

    Returns:
        Flask: Configured Flask app instance.
    """
    app = Flask(__name__, instance_relative_config=False)
    app.config.from_object('config.Config')
    if test_config:
        app.config.update(test_config)

    # Initialise extensions
    db.init_app(app)
    csrf.init_app(app)
    login_manager.init_app(app)

    # Register blueprints
    from .main import main_bp
    from .auth import auth_bp
    from .management import management_bp
    from .api import api_bp
    from . import models

    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(management_bp)
    app.register_blueprint(api_bp)

    @app.errorhandler(HTTPException)
    def handle_http_error(error):
        if request.path.startswith("/api/"):
            return jsonify(
                {
                    "error": {
                        "code": error.name.lower().replace(" ", "_"),
                        "message": error.description,
                    }
                }
            ), error.code
        return error

    @app.errorhandler(CSRFError)
    def handle_csrf_error(error):
        if request.path.startswith("/api/"):
            return jsonify(
                {
                    "error": {
                        "code": "csrf_error",
                        "message": "A valid CSRF token is required.",
                    }
                }
            ), 400
        return error

    @app.errorhandler(Exception)
    def handle_unexpected_error(error):
        if request.path.startswith("/api/"):
            app.logger.exception("Unhandled API error")
            return jsonify(
                {
                    "error": {
                        "code": "internal_server_error",
                        "message": "An unexpected error occurred.",
                    }
                }
            ), 500
        raise error

    with app.app_context():
        if db.engine.dialect.name == "sqlite":
            event.listen(db.engine, "connect", _enable_sqlite_foreign_keys)
        db.create_all()
        _upgrade_legacy_users_table()
        _upgrade_phase3_schema()
        _upgrade_phase4_schema()
        _upgrade_phase7_schema()

    return app
