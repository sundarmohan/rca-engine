from urllib.parse import unquote, urlsplit

import pytest

from scripts.render_runtime import normalize_database_url
from scripts.configure_postgres import APP_RULE, configured_hba, password_verifier
from scripts.render_runtime import main as render_runtime


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


def test_postgres_rule_is_scoped_idempotent_and_preserves_other_rules():
    original = "local all all peer\nhost all all 127.0.0.1/32 ident\n"
    updated = configured_hba(original)
    assert updated == APP_RULE + "\n" + original
    assert configured_hba(updated) == updated


def test_development_password_verifier_uses_salted_scram_without_plaintext():
    password = "Dev[p@ss]#word"
    verifier = password_verifier(password)
    assert verifier.startswith("SCRAM-SHA-256$4096:")
    assert password not in verifier
    assert password_verifier(password) != verifier


@pytest.fixture
def public_runtime(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for name, value in {
        "APP_ENV": "development", "APP_BIND_HOST": "0.0.0.0", "WEBHOOK_TOKEN": "w" * 32,
        "OPERATOR_TOKEN": "o" * 32, "RCA_PUBLIC_DOMAIN": "rca.example.com", "RCA_TLS_EMAIL": "ops@example.com",
        "DATABASE_URL": "postgresql://rca_engine_user:fake@127.0.0.1:5432/rca_engine_db",
    }.items():
        monkeypatch.setenv(name, value)
    return tmp_path


def test_public_ingress_forces_private_application_listener(public_runtime):
    import json

    render_runtime()
    assert json.loads((public_runtime / "runtime.json").read_text())["APP_BIND_HOST"] == "127.0.0.1"


@pytest.mark.parametrize("name,value", [
    ("OPERATOR_TOKEN", "w" * 32), ("OPERATOR_TOKEN", "short"),
    ("RCA_PUBLIC_DOMAIN", "https://rca.example.com"), ("RCA_PUBLIC_DOMAIN", "rca.-invalid.com"),
    ("RCA_PUBLIC_DOMAIN", "rca.example.com/path"), ("RCA_TLS_EMAIL", "ops{$INJECTION}@example.com"),
])
def test_unsafe_public_ingress_configuration_is_rejected(public_runtime, monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(SystemExit):
        render_runtime()
    assert not (public_runtime / "runtime.json").exists()
