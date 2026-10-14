from uuid import uuid4

from tests.conftest import login
from tests.test_support_attachments_local import PNG
from tests.test_support_ticket_center import _valid_ticket, _wait_for_ticket_delivery


def test_optional_screenshot_upload_download_and_duplicate(client, app):
    login(client, "admin@local")
    client.post("/admin/suporte/chamados", data=_valid_ticket())
    ticket = _wait_for_ticket_delivery(app)[0]
    attachment_id = str(uuid4())
    url = f"/admin/suporte/chamados/{ticket.id}/anexos"
    for _ in range(2):
        response = client.post(url, data={"attachment_id": attachment_id},
            files={"file": ("Screenshot.png", PNG, "image/png")}, follow_redirects=False)
        assert response.status_code == 303
    assert len(app.state.control_center_repository.list_ticket_attachments(ticket.id)) == 1
    downloaded = client.get(url + "/" + attachment_id)
    assert downloaded.status_code == 200 and downloaded.content == PNG
    assert downloaded.headers["content-disposition"].startswith("attachment;")
    assert downloaded.headers["x-content-type-options"] == "nosniff"
    assert client.get(url + "/" + str(uuid4())).status_code == 404
    assert client.get("/admin/suporte/chamados/foreign/anexos/" + attachment_id).status_code == 404
    page = client.get(f"/admin/suporte/chamados/{ticket.id}")
    assert "Screenshot.png" in page.text


def test_screenshot_rejects_traversal_forgery_and_oversize(client, app):
    login(client, "admin@local")
    client.post("/admin/suporte/chamados", data=_valid_ticket())
    ticket = _wait_for_ticket_delivery(app)[0]
    url = f"/admin/suporte/chamados/{ticket.id}/anexos"
    for name in ("../capture.png", "capture.svg", "capture.html"):
        result = client.post(url, data={"attachment_id": str(uuid4())},
            files={"file": (name, PNG, "image/png")})
        assert result.status_code == 422
    forged = client.post(url, data={"attachment_id": str(uuid4()), "tenant_id": "foreign"},
        files={"file": ("capture.png", PNG, "image/png")})
    assert forged.status_code == 422
    large = client.post(url, data={"attachment_id": str(uuid4())},
        files={"file": ("capture.png", b"x" * (1024 * 1024 + 20000), "image/png")})
    assert large.status_code == 413
    assert not app.state.control_center_repository.list_ticket_attachments(ticket.id)
