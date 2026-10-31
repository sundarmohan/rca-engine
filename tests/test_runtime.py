from urllib.parse import unquote, urlsplit

import pytest

from scripts.render_runtime import normalize_database_url


@pytest.mark.parametrize("password", ["Dev[p@ss]#word/?:+", "Dev%word", "Dev word", "dev:pass"])
def test_raw_password_special_characters_are_encoded(password):
    result = normalize_database_url(f"postgresql://rca_engine_user:{password}@127.0.0.1:5432/rca_engine_db?sslmode=disable")
    parsed = urlsplit(result)
    assert unquote(parsed.password) == password
    assert parsed.hostname == "127.0.0.1"
    assert parsed.path == "/rca_engine_db"
    assert parsed.query == "sslmode=disable"


def test_encoded_password_is_not_double_encoded():
    value = "postgresql://rca_engine_user:Dev%40%5Bword%5D@127.0.0.1:5432/rca_engine_db"
    assert normalize_database_url(value) == value


def test_assignment_quotes_and_old_container_alias_are_normalized():
    value = 'DATABASE_URL="postgresql://rca_engine_user:Dev[pass]@host.docker.internal:5432/rca_engine_db"'
    assert normalize_database_url(value) == "postgresql://rca_engine_user:Dev%5Bpass%5D@127.0.0.1:5432/rca_engine_db"


def test_unexpected_database_target_is_not_rewritten():
    value = "postgresql://other:pass@external.example:5432/other"
    assert normalize_database_url(value) == value
