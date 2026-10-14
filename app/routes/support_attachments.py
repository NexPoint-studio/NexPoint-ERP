"""Explicit optional screenshots. Files stay private and are never rendered inline."""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from starlette.datastructures import UploadFile

from app.core.permissions import require_permission
from app.repositories.sync import OutboxRepository
from app.services.admin_lock import require_admin_unlock
from app.services.support import SUPPORT_PERMISSION
from app.services.support_tickets import client_support_identity, ensure_control_center_repository
from app.services.sync_remote import SyncRemoteError
from app.services.support_attachment_queue import ensure_local_attachment_ticket, enqueue_attachment_reference
from control_center.attachments import MAX_ATTACHMENT_BYTES, validate_attachment
from control_center.domain import ControlCenterError, ControlCenterNotFoundError

router = APIRouter(prefix="/admin/suporte/chamados", dependencies=[Depends(require_admin_unlock)])


def download_response(attachment):
    # ASCII basename validated at ingestion; attachment prevents active content rendering.
    validate_attachment(attachment.name, attachment.content_type, attachment.data)
    return Response(attachment.data, media_type=attachment.content_type, headers={
        "Content-Disposition": f'attachment; filename="{attachment.name}"',
        "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store",
        "Content-Security-Policy": "sandbox; default-src 'none'",
    })


@router.post("/{ticket_id}/anexos")
async def upload(request: Request, ticket_id: str):
    require_permission(request, SUPPORT_PERMISSION)
    # Bound the complete multipart stream before its file spooler consumes it.
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_ATTACHMENT_BYTES + 16 * 1024:
            raise HTTPException(413, "O anexo excede o limite de 1 MiB.")
        chunks.append(chunk)
    request._body = b"".join(chunks)
    async with request.form(max_files=1, max_fields=3, max_part_size=MAX_ATTACHMENT_BYTES + 1024) as form:
        if set(form.keys()) - {"file", "attachment_id", "submit"}:
            raise HTTPException(422, "Campos inválidos.")
        uploaded = form.get("file")
        attachment_id = str(form.get("attachment_id", ""))
        try:
            if str(UUID(attachment_id)) != attachment_id or not isinstance(uploaded, UploadFile):
                raise ValueError()
            data = await uploaded.read(MAX_ATTACHMENT_BYTES + 1)
            name, content_type, data, digest = validate_attachment(uploaded.filename, uploaded.content_type, data)
        except (ValueError, ControlCenterError):
            raise HTTPException(422, "Use PNG, JPEG ou WebP até 1 MiB, com nome simples sem acentos.") from None
    with request.app.state.session_factory() as session:
        identity = client_support_identity(request, session)
        repo = ensure_control_center_repository(request.app)
        try:
            outbox = OutboxRepository(session)
            ensure_local_attachment_ticket(repo, identity, outbox, ticket_id)
            repo.add_ticket_attachment(ticket_id, attachment_id=attachment_id, name=name,
                content_type=content_type, data=data, tenant_id=identity.tenant_id,
                installation_id=identity.installation_id)
            # Durable local bytes precede the outbox reference. Retrying after a crash is idempotent.
            enqueue_attachment_reference(outbox, identity, ticket_id, attachment_id)
            session.commit()
        except ControlCenterNotFoundError:
            raise HTTPException(404, "Chamado não encontrado.") from None
        except (ControlCenterError, ValueError):
            raise HTTPException(422, "Não foi possível anexar. Limite de três imagens por chamado.") from None
    worker = getattr(request.app.state, "sync_worker", None)
    if worker:
        worker.wake()
    return RedirectResponse(f"/admin/suporte/chamados/{ticket_id}", status_code=303)


@router.get("/{ticket_id}/anexos/{attachment_id}")
def download(request: Request, ticket_id: str, attachment_id: str):
    require_permission(request, SUPPORT_PERMISSION)
    with request.app.state.session_factory() as session:
        identity = client_support_identity(request, session)
        repo = ensure_control_center_repository(request.app)
        try:
            attachment = repo.get_ticket_attachment(ticket_id, attachment_id,
                tenant_id=identity.tenant_id, installation_id=identity.installation_id)
            if attachment is None:
                raise ControlCenterNotFoundError("Anexo não encontrado.")
        except ControlCenterNotFoundError:
            remote = getattr(request.app.state, "support_attachment_remote", None)
            if remote is None:
                raise HTTPException(404, "Anexo não encontrado.") from None
            try:
                attachment = remote.transfer("download", ticket_id, attachment_id)[0]
            except SyncRemoteError:
                raise HTTPException(503, "Não foi possível baixar o anexo. Tente quando houver conexão.") from None
        except ControlCenterError:
            raise HTTPException(422, "Integridade do anexo inválida.") from None
    return download_response(attachment)
