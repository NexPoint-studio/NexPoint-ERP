from __future__ import annotations

import json

import pytest

from app.core import config as config_module


def test_prod_installation_config_reads_only_nonsecret_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    path = tmp_path / "NexPoint" / "ERP" / "installation.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "schema_version": 1,
        "company_name": "NexPoint",
        "tenant_type": "INTERNAL",
        "supabase_project_ref": config_module.PRODUCTION_SUPABASE_PROJECT_REF,
    }), encoding="utf-8")

    config = config_module._production_installation_config()

    assert config["company_name"] == "NexPoint"
    assert config["tenant_type"] == "INTERNAL"
    assert set(config) == {
        "schema_version", "company_name", "tenant_type", "supabase_project_ref"
    }

    executable = tmp_path / "bundle" / "NexPointERP.exe"
    executable.parent.mkdir()
    executable.touch()
    (executable.parent / "build-manifest.json").write_text(json.dumps({
        "schema_version": 1,
        "version": "1.0.0",
        "build": "PROD-abcdef123456",
        "commit": "a" * 40,
        "environment": "production",
        "channel": "PROD",
        "supabase_project_ref": config_module.PRODUCTION_SUPABASE_PROJECT_REF,
    }), encoding="utf-8")
    monkeypatch.setattr(config_module.sys, "frozen", True, raising=False)
    monkeypatch.setattr(config_module.sys, "executable", str(executable))
    monkeypatch.delenv("ERP_COMPANY_NAME", raising=False)
    monkeypatch.delenv("ERP_TENANT_TYPE", raising=False)
    settings = config_module.get_settings()
    assert settings.company_name == "NexPoint"
    assert settings.tenant_type == "INTERNAL"


@pytest.mark.parametrize("extra", [
    {"service_role": "never-accepted"},
    {"tenant_type": "TEST"},
    {"tenant_type": []},
    {"supabase_project_ref": "abcdefghijklmnopqrst"},
])
def test_prod_installation_config_rejects_secrets_and_invalid_identity(
    tmp_path, monkeypatch, extra
):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    path = tmp_path / "NexPoint" / "ERP" / "installation.json"
    path.parent.mkdir(parents=True)
    payload = {
        "schema_version": 1,
        "company_name": "NexPoint",
        "tenant_type": "INTERNAL",
        "supabase_project_ref": config_module.PRODUCTION_SUPABASE_PROJECT_REF,
        **extra,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RuntimeError, match="configuracao da instalacao PROD"):
        config_module._production_installation_config()
