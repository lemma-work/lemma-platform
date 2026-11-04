"""A web form row with files attached: the multipart door ``widget.js`` uses.

Who may send is settled before the body is read; past that, each file is held
to its own cap and the whole response to another, a file for a column the
table did not open is refused, and an empty file input is no attachment.
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from app.core.api.exception_handlers import register_exception_handlers
from app.modules.agent_surfaces.api import public_dependencies
from app.modules.agent_surfaces.api.controllers import public_web_controller
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.web_widgets import refused
from app.modules.datastore.contracts.public_rows import (
    OpenTable,
    PublicAudience,
    PublicColumn,
)

pytestmark = pytest.mark.unit

APPLICATIONS = OpenTable(
    pod_id=uuid4(),
    name="applications",
    audience=PublicAudience.ANYONE,
    contact_owned=False,
    columns=(
        PublicColumn(name="full_name", type="TEXT", required=True),
        PublicColumn(name="cv", type="FILE_PATH", required=True, input="file"),
        PublicColumn(name="portfolio", type="FILE_PATH", required=False, input="file"),
    ),
)
URL = "/public/web/pk_test/rows/with-files?table=applications"


class _Chat:
    def __init__(self, *, admit_error=None) -> None:
        self.admit_error = admit_error
        self.saved: list[dict[str, object]] = []

    async def admit_row(self, widget, visitor, *, table, address):
        if self.admit_error is not None:
            raise self.admit_error
        assert table == "applications"
        return APPLICATIONS

    async def save_visitor_row(self, widget, visitor, *, table, answers, attachments):
        # The staged files are gone once the request ends: read them now.
        self.saved.append(
            {
                "answers": answers,
                "files": {
                    name: (file.name, file.content.read_bytes())
                    for name, file in attachments.items()
                },
            }
        )


def _client(chat: _Chat) -> TestClient:
    api = FastAPI()
    register_exception_handlers(api)
    api.include_router(public_web_controller.router)
    api.dependency_overrides[public_dependencies.public_widget] = lambda: object()
    api.dependency_overrides[public_dependencies.optional_visitor] = lambda: None
    api.dependency_overrides[public_dependencies.visitor_address] = lambda: "1.2.3.4"
    api.dependency_overrides[public_dependencies.web_chat] = lambda: chat
    return TestClient(api, raise_server_exceptions=False)


def _values(**answers) -> dict[str, str]:
    return {"values": json.dumps(answers)}


def test_typed_answers_and_files_reach_the_row_together():
    chat = _Chat()
    response = _client(chat).post(
        URL,
        data=_values(full_name="Ana"),
        files={"cv": ("cv.pdf", b"%PDF-1.7", "application/pdf")},
    )
    assert response.status_code == 201, response.text
    assert chat.saved == [
        {"answers": {"full_name": "Ana"}, "files": {"cv": ("cv.pdf", b"%PDF-1.7")}}
    ]


def test_an_empty_file_input_is_no_attachment():
    chat = _Chat()
    response = _client(chat).post(
        URL,
        data=_values(full_name="Ana"),
        files={
            "cv": ("cv.pdf", b"%PDF-1.7", "application/pdf"),
            "portfolio": ("", b"", "application/octet-stream"),
        },
    )
    assert response.status_code == 201, response.text
    assert set(chat.saved[0]["files"]) == {"cv"}


def test_a_file_over_its_cap_is_refused_by_name(monkeypatch):
    monkeypatch.setattr(surface_settings, "surface_web_attachment_max_bytes", 1024)
    chat = _Chat()
    response = _client(chat).post(
        URL,
        data=_values(full_name="Ana"),
        files={"cv": ("cv.pdf", b"x" * 4096, "application/pdf")},
    )
    assert response.status_code == 413
    assert response.json()["code"] == "too_large"
    assert response.json()["message"].startswith("Cv is too big")
    assert chat.saved == []


def test_a_response_over_its_cap_is_refused_whole(monkeypatch):
    monkeypatch.setattr(surface_settings, "surface_web_attachments_max_bytes", 2048)
    chat = _Chat()
    response = _client(chat).post(
        URL,
        data=_values(full_name="Ana"),
        files={
            "cv": ("cv.pdf", b"x" * 1500, "application/pdf"),
            "portfolio": ("p.pdf", b"x" * 1500, "application/pdf"),
        },
    )
    assert response.status_code == 413
    assert "in all" in response.json()["message"]
    assert chat.saved == []


def test_a_file_for_a_typed_column_is_refused():
    chat = _Chat()
    response = _client(chat).post(
        URL,
        data=_values(),
        files={"full_name": ("name.txt", b"Ana", "text/plain")},
    )
    assert response.status_code == 400
    assert response.json()["code"] == "bad_answer"
    assert chat.saved == []


def test_answers_that_are_not_an_object_are_refused():
    chat = _Chat()
    response = _client(chat).post(
        URL,
        data={"values": "[1, 2]"},
        files={"cv": ("cv.pdf", b"%PDF", "application/pdf")},
    )
    assert response.status_code == 400
    assert response.json()["code"] == "bad_answer"
    assert chat.saved == []


def test_a_visitor_turned_away_is_turned_away_before_the_body_is_read():
    chat = _Chat(admit_error=refused("Confirm your email first", 403, "needs_contact"))
    response = _client(chat).post(
        URL,
        data=_values(full_name="Ana"),
        files={"cv": ("cv.pdf", b"%PDF", "application/pdf")},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "needs_contact"
    assert chat.saved == []


def test_strangers_uploads_never_take_a_members_upload_slot():
    from app.core.api.streaming_multipart import upload_staging_coordinator

    assert public_web_controller._visitor_uploads is not upload_staging_coordinator


def test_answers_too_long_are_said_as_answers_not_files():
    chat = _Chat()
    response = _client(chat).post(
        URL,
        data={"values": json.dumps({"full_name": "x" * 70_000})},
        files={"cv": ("cv.pdf", b"%PDF", "application/pdf")},
    )
    assert response.status_code == 413
    assert response.json()["message"] == "Your answers are too long"
    assert chat.saved == []
