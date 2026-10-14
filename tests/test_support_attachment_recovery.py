from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import delete

from app.models.sync import OutboxItem
from app.repositories.sync import OutboxRepository
from app.services.support_attachment_queue import AttachmentOutboxReconciler
from app.services.support_tickets import client_support_identity
from tests.conftest import login
from tests.test_support_attachments_local import PNG
from tests.test_support_ticket_center import _valid_ticket, _wait_for_ticket_delivery


def test_attachment_saved_before_outbox_commit_is_recovered_after_restart(client, app):
    login(client, "admin@local")
    client.post("/admin/suporte/chamados", data=_valid_ticket())
    ticket = _wait_for_ticket_delivery(app)[0]
    app.state.sync_worker.stop()
    repository = app.state.control_center_repository
    attachment_id = str(uuid4())
    # Simulate a crash after the sidecar commit but before the operational commit.
    repository.add_ticket_attachment(ticket.id, attachment_id=attachment_id,
        name="capture.png", content_type="image/png", data=PNG)
    with app.state.session_factory() as session:
        identity = client_support_identity(SimpleNamespace(app=app), session)
        assert not OutboxRepository(session).list_unsynced(event_type="support_operation")
    reconciler = AttachmentOutboxReconciler(app)
    assert reconciler.run() == 1
    assert reconciler.run() == 1  # same durable reference, no duplicate
    with app.state.session_factory() as session:
        items = OutboxRepository(session).list_unsynced(event_type="support_operation")
        assert len(items) == 1
        assert "data_base64" not in items[0].payload_json
    app.state.sync_engine.run_once()
    assert not repository.list_undelivered_attachment_references(identity.tenant_id, identity.installation_id)
    # A later outbox retention cleanup must not enqueue an already delivered file.
    with app.state.session_factory() as session:
        session.execute(delete(OutboxItem).where(OutboxItem.idempotency_key == "support-attachment:" + attachment_id))
        session.commit()
    assert AttachmentOutboxReconciler(app).run() == 0
    assert repository.get_ticket_attachment(ticket.id, attachment_id).data == PNG
