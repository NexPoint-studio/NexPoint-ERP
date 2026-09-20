from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from app.core.modules import MODULE_BY_ID
from app.core.permissions import require_permission
from app.services.admin_lock import require_admin_unlock
from app.repositories import AuthRepository, ConfigurationRepository
from app.routes.helpers import navigation_context, templates
from app.services.system_info import collect_system_information
from app.services.system_maintenance import (
    BACKUP_PERMISSION,
    RESTORE_PERMISSION,
    MaintenanceAuthorizationError,
    MaintenanceConflictError,
    MaintenanceError,
    MaintenanceNotFoundError,
    MaintenanceValidationError,
    SystemMaintenanceService,
)


router = APIRouter(prefix="/admin/sistema", dependencies=[Depends(require_admin_unlock)])


def _service(request: Request) -> SystemMaintenanceService:
    state = request.app.state
    try:
        database_path = Path(state.database_path)
        backup_root = Path(state.backup_root)
    except AttributeError as error:
        raise RuntimeError("A manutenção local não foi configurada no aplicativo.") from error
    return SystemMaintenanceService(
        database_path=database_path,
        backup_root=backup_root,
        settings=state.settings,
        session_factory=state.session_factory,
    )


def _flash(request: Request, kind: str, message: str) -> None:
    request.session["system_flash"] = {"kind": kind, "message": message[:300]}


def _take_flash(request: Request) -> dict[str, str] | None:
    value = request.session.pop("system_flash", None)
    if not isinstance(value, dict):
        return None
    if value.get("kind") not in {"success", "error"} or not isinstance(value.get("message"), str):
        return None
    return {"kind": value["kind"], "message": value["message"][:300]}


def _require_fields(form, allowed: set[str]) -> None:
    if {str(key) for key in form.keys()} - allowed:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="O formulário contém campos não permitidos.",
        )


def _status_for(error: MaintenanceError) -> int:
    if isinstance(error, MaintenanceAuthorizationError):
        return status.HTTP_403_FORBIDDEN
    if isinstance(error, MaintenanceNotFoundError):
        return status.HTTP_404_NOT_FOUND
    if isinstance(error, MaintenanceConflictError):
        return status.HTTP_409_CONFLICT
    if isinstance(error, MaintenanceValidationError):
        return status.HTTP_422_UNPROCESSABLE_CONTENT
    return status.HTTP_500_INTERNAL_SERVER_ERROR


def _render_system(
    request: Request,
    *,
    error: str | None = None,
    status_code: int = 200,
):
    actor = require_permission(request, "admin.system.view")
    service = _service(request)
    snapshot = service.current_snapshot()
    restore_disabled = bool(getattr(request.app.state, "demo_restore_disabled", False))
    backups = service.list_backups(actor_id=actor.id) if actor.can(BACKUP_PERMISSION) else []
    pending = (
        service.pending_restore()
        if not restore_disabled and actor.can(RESTORE_PERMISSION) and "admin" in actor.roles
        else None
    )
    with request.app.state.session_factory() as session:
        persisted = ConfigurationRepository(session).settings()
        module = MODULE_BY_ID["admin"]
        tab = next(item for item in module.tabs if item.id == "sistema")
        information = collect_system_information(
            settings=request.app.state.settings,
            persisted_settings=persisted,
            database_path=Path(request.app.state.database_path),
            schema_version=snapshot.schema_version,
        )
        context = navigation_context(
            request,
            session,
            selected_module=module,
            selected_tab=tab,
            page_title="Sistema local",
            implemented=True,
            information=information,
            backups=backups,
            pending_restore=pending,
            restore_disabled=restore_disabled,
            flash=_take_flash(request),
            system_error=error,
        )
        return templates.TemplateResponse(
            request,
            "admin/system.html",
            context,
            status_code=status_code,
        )


@router.get("")
def system_info(request: Request):
    with request.app.state.session_factory() as session:
        AuthRepository(session).audit(
            request.state.current_user.id, "admin.navigate", request.url.path, "sistema"
        )
    return _render_system(request)


@router.post("/backups")
async def create_backup(request: Request):
    actor = require_permission(request, BACKUP_PERMISSION)
    form = await request.form()
    _require_fields(form, {"password"})
    try:
        record = await run_in_threadpool(
            _service(request).create_backup,
            actor_id=actor.id,
            password=str(form.get("password") or ""),
        )
    except MaintenanceError as error:
        return _render_system(request, error=str(error), status_code=_status_for(error))
    _flash(request, "success", f"Backup {record.filename} criado e verificado.")
    return RedirectResponse("/admin/sistema", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/backups/{backup_id}/download")
def download_backup(request: Request, backup_id: str):
    actor = require_permission(request, BACKUP_PERMISSION)
    try:
        record, path = _service(request).backup_for_download(
            backup_id=backup_id,
            actor_id=actor.id,
        )
    except MaintenanceError as error:
        raise HTTPException(status_code=_status_for(error), detail=str(error)) from error
    return FileResponse(
        path,
        filename=record.filename,
        media_type="application/vnd.sqlite3",
        headers={
            "Cache-Control": "no-store, max-age=0",
            "Pragma": "no-cache",
        },
    )


@router.post("/restauracao")
async def schedule_restore(request: Request):
    if getattr(request.app.state, "demo_restore_disabled", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="A restauração direta é desativada no modo demo.",
        )
    actor = require_permission(request, RESTORE_PERMISSION)
    form = await request.form()
    _require_fields(form, {"backup_file", "password", "confirmation"})
    uploaded = form.get("backup_file")
    if not isinstance(uploaded, UploadFile):
        error = MaintenanceValidationError("Selecione um arquivo .sqlite3 para restaurar.")
        return _render_system(request, error=str(error), status_code=_status_for(error))
    await uploaded.seek(0)
    try:
        plan = await run_in_threadpool(
            _service(request).schedule_restore,
            actor_id=actor.id,
            password=str(form.get("password") or ""),
            confirmation=str(form.get("confirmation") or ""),
            upload_stream=uploaded.file,
            original_filename=uploaded.filename or "",
        )
    except MaintenanceError as error:
        return _render_system(request, error=str(error), status_code=_status_for(error))
    finally:
        await uploaded.close()
    _flash(
        request,
        "success",
        f"Restauração {plan.operation_id} validada. Feche e abra o ERP para aplicá-la.",
    )
    return RedirectResponse("/admin/sistema", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/restauracao/cancelar")
async def cancel_restore(request: Request):
    if getattr(request.app.state, "demo_restore_disabled", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="A restauração direta é desativada no modo demo.",
        )
    actor = require_permission(request, RESTORE_PERMISSION)
    form = await request.form()
    _require_fields(form, {"password"})
    try:
        await run_in_threadpool(
            _service(request).cancel_restore,
            actor_id=actor.id,
            password=str(form.get("password") or ""),
        )
    except MaintenanceError as error:
        return _render_system(request, error=str(error), status_code=_status_for(error))
    _flash(request, "success", "A restauração pendente foi cancelada.")
    return RedirectResponse("/admin/sistema", status_code=status.HTTP_303_SEE_OTHER)
