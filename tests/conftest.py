import pytest

from server.app import create_app

TEST_ADMIN_USERNAME = "admin"
TEST_ADMIN_PASSWORD = "test-password"


@pytest.fixture
def app(tmp_path):
    return create_app(
        data_dir=tmp_path,
        admin_username=TEST_ADMIN_USERNAME,
        admin_password=TEST_ADMIN_PASSWORD,
        secret_key="test-secret-key",
    )


@pytest.fixture
def client(app):
    return app.test_client()
