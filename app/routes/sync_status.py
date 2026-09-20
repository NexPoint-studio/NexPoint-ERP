from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.repositories.sync import OutboxRepository


router = APIRouter()


@router.get("/sync/status", include_in_schema=False)
def sync_status(request: Request) -> JSONResponse:
    """Retorna somente o estado técnico mínimo usado pelo indicador local."""

    with request.app.state.session_factory() as session:
        counts = OutboxRepository(session).counts()
    connectivity = request.app.state.connectivity_service.snapshot()
    waiting = sum(counts.get(name, 0) for name in ("pending", "sending", "failed"))
    return JSONResponse(
        {
            "state": connectivity.state.value,
            "waiting": waiting,
            "sending": counts.get("sending", 0),
            "dead_letter": counts.get("dead_letter", 0),
            "synced": counts.get("synced", 0),
        },
        headers={"Cache-Control": "no-store"},
    )
