from pathlib import Path

import pytest

from scripts import verify_distribution_secrets as scan


def test_public_configuration_is_not_treated_as_secret(tmp_path):
    (tmp_path / ".env.local").write_text(
        "ERP_LOGO_PATH=/static/img/logo-placeholder.svg\n"
        "ERP_SUPABASE_PROJECT_REF=abcdefghijklmnopqrst\n"
        "GROQ_MODEL=openai/gpt-oss-120b\n",
        encoding="utf-8",
    )
    assert scan.local_secret_values(tmp_path) == ()


@pytest.mark.parametrize("name", [
    "ERP_ADMIN_PASSWORD", "CONTROL_CENTER_SUPABASE_SERVICE_ROLE_KEY",
    "GROQ_API_KEY", "GEMINI_API_KEY", "NEXA_ERP_BRIDGE_SECRET",
])
def test_actual_secret_variables_remain_scanned(tmp_path, name):
    value = "synthetic-distribution-scan-value"
    (tmp_path / ".env.local").write_text(f"{name}={value}\n", encoding="utf-8")
    assert scan.local_secret_values(tmp_path) == (value.encode(),)


def test_checksums_are_scanned_for_actual_secret_values(tmp_path, monkeypatch, capsys):
    value = "synthetic-distribution-scan-value"
    (tmp_path / ".env.local").write_text(f"ERP_ADMIN_PASSWORD={value}\n", encoding="utf-8")
    distribution = tmp_path / "dist"
    distribution.mkdir()
    (distribution / "SHA256SUMS.txt").write_text(value, encoding="utf-8")
    monkeypatch.setattr(scan, "__file__", str(tmp_path / "scripts" / "verify_distribution_secrets.py"))
    monkeypatch.setattr(scan.sys, "argv", ["scan", "--distribution", str(distribution)])
    assert scan.main() == 3
    assert value not in capsys.readouterr().err
