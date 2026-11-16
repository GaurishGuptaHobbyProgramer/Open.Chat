import os
import tempfile
from pathlib import Path


def main():
    with tempfile.TemporaryDirectory(prefix="openchat-signup-check-") as temp_dir:
        temp_path = Path(temp_dir)
        os.environ["DATABASE_URL"] = str(temp_path / "signup-test.db")
        os.environ["APP_ENV"] = "development"
        os.environ["FLASK_DEBUG"] = "false"

        from app import create_app, orm_db

        test_app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "isolated-signup-check",
                "DATABASE": str(temp_path / "signup-test.db"),
                "UPLOAD_FOLDER": str(temp_path / "uploads"),
            }
        )
        try:
            response = test_app.test_client().post(
                "/auth",
                data={
                    "mode": "signup",
                    "username": "TestUser123",
                    "password": "StrongPass123!",
                    "confirm_password": "StrongPass123!",
                },
                follow_redirects=False,
            )
            print(f"STATUS {response.status_code}")
            print(f"LOCATION {response.headers.get('Location')}")
            print("Signup check used an isolated temporary SQLite database.")
            return 0 if response.status_code in {302, 303} else 1
        finally:
            with test_app.app_context():
                orm_db.engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
