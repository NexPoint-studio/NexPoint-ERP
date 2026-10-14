from control_center.domain import SupportTicket, TicketHistoryEntry, TicketInternalNote
from control_center.supabase_repository import SupabaseControlCenterRepository
import json
from urllib.parse import parse_qs, urlsplit


def test_cloud_client_projection_excludes_internal_content_on_every_read(monkeypatch):
    # No transport or credentials: exercise the production repository boundary
    # with deterministic domain objects only.
    repo = object.__new__(SupabaseControlCenterRepository)
    ticket = SupportTicket(id="ticket_local", tenant_id="tenant_local",
        internal_notes=(TicketInternalNote(body="Internal investigation"),),
        history=(
            TicketHistoryEntry(event_type="created", actor_id="operator", detail="internal context"),
            TicketHistoryEntry(event_type="status_changed", to_status="waiting_customer", actor_id="operator"),
            TicketHistoryEntry(event_type="internal_note", detail="Private note"),
            TicketHistoryEntry(event_type="internal_note_added", detail="Legacy note"),
            TicketHistoryEntry(event_type="future_private_event", detail="Unknown event"),
        ))
    monkeypatch.setattr(repo, "get_ticket", lambda _: ticket)
    monkeypatch.setattr(repo, "list_tickets", lambda *a, **kw: (ticket,))
    projected = repo.get_tenant_ticket("tenant_local", "ticket_local")
    assert projected.internal_notes == ()
    assert [event.event_type for event in projected.history] == ["created", "status_changed"]
    assert all(event.actor_id is None and event.detail is None for event in projected.history)
    assert projected.history[1].to_status == "waiting_customer"
    assert repo.list_tenant_tickets("tenant_local") == (projected,)
    assert repo.list_tenant_ticket_history("tenant_local", "ticket_local") == projected.history
    assert repo.get_tenant_ticket("other_tenant", "ticket_local") is None
    assert repo.list_tenant_ticket_history("other_tenant", "ticket_local") == ()
    assert repo.get_ticket("ticket_local").internal_notes == ticket.internal_notes


def test_cloud_public_boundary_with_serialized_backend_rows():
    calls = []
    def transport(method, url, headers, body, timeout):
        calls.append(url)
        path = urlsplit(url).path
        if path.endswith("/np_support_tickets"):
            rows = [{"id": "ticket_local", "tenant_id": "tenant_local",
                     "installation_id": "installation_local", "subject": "Ajuda",
                     "status": "waiting_customer"}]
        elif path.endswith("/np_support_ticket_events"):
            rows = [
                {"id": "event_private", "ticket_id": "ticket_local", "event_type": "internal_note",
                 "metadata": {"author_id": "operator_local", "body": "Internal-only QA marker"}},
                {"id": "event_status", "ticket_id": "ticket_local", "event_type": "status_changed",
                 "metadata": {"actor_id": "operator_local", "detail": "Internal-only QA marker",
                              "from_status": "open", "to_status": "waiting_customer"}},
            ]
        else:
            raise AssertionError(path)
        return 200, {"Content-Type": "application/json"}, json.dumps(rows).encode()

    repo = SupabaseControlCenterRepository(
        "https://abcdefghijklmnopqrst.supabase.co", "sb_secret_" + "FAKE_TEST_ONLY_" * 4,
        transport=transport, initialize=False)
    listed = repo.list_tenant_tickets("tenant_local")
    detail = repo.get_tenant_ticket("tenant_local", "ticket_local")
    history = repo.list_tenant_ticket_history("tenant_local", "ticket_local")
    for result in (listed, detail, history):
        assert "Internal-only QA marker" not in repr(result)
        assert "operator_local" not in repr(result)
        assert "event_private" not in repr(result)
    assert parse_qs(urlsplit(calls[0]).query)["tenant_id"] == ["eq.tenant_local"]
    assert repo.get_ticket("ticket_local").internal_notes[0].body == "Internal-only QA marker"
