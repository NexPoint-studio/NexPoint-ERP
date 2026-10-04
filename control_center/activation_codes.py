"""Platform-only enrollment. Codes exist in memory only until delivered once."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import re
import secrets
from uuid import uuid4

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from control_center.domain import ControlCenterError, ControlCenterValidationError

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{7,119}$")



class ActivationCodeService:
    def __init__(self, repository):
        self.repository = repository

    def _actor(self, actor_id):
        actor = self.repository.get_platform_user(actor_id)
        if actor is None or not actor.active or actor.role != "platform_admin":
            raise ControlCenterValidationError("Somente a administração NexPoint pode emitir códigos.")

    def issue(self, actor_id, tenant_key, installation_key, label, *, hours=24):
        self._actor(actor_id)
        if (not isinstance(tenant_key, str) or len(tenant_key) > 80 or not _IDENTIFIER.fullmatch(tenant_key)
                or not isinstance(installation_key, str) or not _IDENTIFIER.fullmatch(installation_key)
                or not isinstance(label, str) or not 1 <= len(label.strip()) <= 160
                or any(ord(c) < 32 or ord(c) == 127 for c in label)
                or type(hours) is not int or not 1 <= hours <= 168):
            raise ControlCenterValidationError("Confira empresa, instalação e validade.")
        code = secrets.token_urlsafe(32)
        digest = sha256(code.encode()).hexdigest()
        if hasattr(self.repository, "_rpc"):
            result = self.repository._rpc_success(self.repository._rpc("np_admin_issue_installation_code", {
                "p_actor_id": actor_id, "p_tenant_key": tenant_key, "p_installation_key": installation_key,
                "p_installation_label": label.strip(), "p_code_digest": digest, "p_hours": hours,
            }), "emissão de ativação")
        else:
            now = datetime.now(timezone.utc)
            with self.repository._write() as db:
                actor = db.execute("SELECT active,role FROM platform_users WHERE id=?", (actor_id,)).fetchone()
                tenant = db.execute("SELECT * FROM tenants WHERE id=?", (tenant_key,)).fetchone()
                if not actor or not actor["active"] or actor["role"] != "platform_admin" or not tenant or tenant["status"] != "active":
                    raise ControlCenterValidationError("Empresa ou administrador indisponível.")
                if db.execute("SELECT 1 FROM installations WHERE installation_id=?", (installation_key,)).fetchone():
                    raise ControlCenterValidationError("Esta instalação já existe.")
                revoked = db.execute("UPDATE installation_activation_codes SET revoked_at=? WHERE installation_key=? AND revoked_at IS NULL AND consumed_at IS NULL RETURNING id", (now.isoformat(), installation_key)).fetchall()
                for row in revoked:
                    db.execute("INSERT INTO installation_activation_events VALUES(?,?,?,?,?)",
                               (str(uuid4()), row["id"], actor_id, "revoked", now.isoformat()))
                result = {"id": str(uuid4()), "tenant_key": tenant_key, "installation_key": installation_key,
                          "company_name": tenant["display_name"], "expires_at": (now + timedelta(hours=hours)).isoformat()}
                db.execute("INSERT INTO installation_activation_codes(id,code_digest,tenant_id,installation_key,installation_label,company_name,created_by,created_at,expires_at) VALUES(?,?,?,?,?,?,?,?,?)",
                           (result["id"], digest, tenant_key, installation_key, label.strip(), result["company_name"], actor_id, now.isoformat(), result["expires_at"]))
                db.execute("INSERT INTO installation_activation_events VALUES(?,?,?,?,?)",
                           (str(uuid4()), result["id"], actor_id, "issued", now.isoformat()))
        return {**result, "activation_code": code}

    def revoke(self, actor_id, code_id):
        self._actor(actor_id)
        if hasattr(self.repository, "_rpc"):
            return self.repository._rpc_success(self.repository._rpc("np_admin_revoke_installation_code", {
                "p_actor_id": actor_id, "p_code_id": code_id}), "revogação de ativação")
        with self.repository._write() as db:
            actor = db.execute("SELECT active,role FROM platform_users WHERE id=?", (actor_id,)).fetchone()
            if not actor or not actor["active"] or actor["role"] != "platform_admin":
                raise ControlCenterValidationError("Administrador indisponível.")
            now = datetime.now(timezone.utc).isoformat()
            changed = db.execute("UPDATE installation_activation_codes SET revoked_at=? WHERE id=? AND revoked_at IS NULL", (now, code_id)).rowcount
            if changed:
                db.execute("INSERT INTO installation_activation_events VALUES(?,?,?,?,?)",
                           (str(uuid4()), code_id, actor_id, "revoked", now))

    def list_codes(self, actor_id):
        self._actor(actor_id)
        columns = "id,tenant_id,installation_key,installation_label,company_name,expires_at,revoked_at,consumed_at"
        if hasattr(self.repository, "_select"):
            return self.repository._select("np_installation_activation_codes", select=columns, order="created_at.desc", limit=100)
        with self.repository._read() as db:
            return [dict(row) for row in db.execute(
                f"SELECT {columns} FROM installation_activation_codes ORDER BY created_at DESC LIMIT 100"
            )]


def build_activation_router(repository, *, templates, base_context, require_csrf, platform_admin_only):
    """Mount with existing CC session middleware; callbacks preserve its policy."""
    router = APIRouter()
    service = ActivationCodeService(repository)

    @router.get("/ativacoes")
    def page(request: Request):
        platform_admin_only(request)
        return templates.TemplateResponse(request, "activation_code.html", base_context(
            request, "installations", "Ativar computador", issued=None, error=None,
            codes=service.list_codes(request.state.platform_user.id)), headers={"Cache-Control": "no-store"})

    @router.post("/ativacoes")
    def issue(request: Request, tenant_key: str = Form(..., max_length=80),
              installation_key: str = Form(..., max_length=120), label: str = Form(..., max_length=160),
              hours: int = Form(24), csrf: str = Form("", alias="_csrf", max_length=128)):
        require_csrf(request, csrf)
        platform_admin_only(request)
        try:
            issued = service.issue(request.state.platform_user.id, tenant_key, installation_key, label, hours=hours)
        except ControlCenterError:
            raise HTTPException(status_code=422, detail="Não foi possível emitir o código. Confira a empresa e a instalação.") from None
        return templates.TemplateResponse(request, "activation_code.html", base_context(
            request, "installations", "Código de ativação", issued=issued, error=None, codes=()),
            headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})

    @router.post("/ativacoes/{code_id}/revogar")
    def revoke(request: Request, code_id: str, csrf: str = Form("", alias="_csrf", max_length=128)):
        require_csrf(request, csrf)
        platform_admin_only(request)
        try:
            service.revoke(request.state.platform_user.id, code_id)
        except ControlCenterError:
            raise HTTPException(status_code=422, detail="Não foi possível revogar este código.") from None
        return RedirectResponse("/ativacoes", status_code=303)

    return router
