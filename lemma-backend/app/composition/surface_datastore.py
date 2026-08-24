"""Datastore adapters used by inbound surface attachments and outbound cards.

The record/table pair is here for the same reason the file service is: a card
delivered to Telegram or WhatsApp shows a table's own first rows, and reading
them is a datastore call the surfaces module makes through this root rather
than by importing into another module's internals.
"""

from app.modules.datastore.api.dependencies import (
    build_file_service,
    build_record_service,
    build_table_service,
)

__all__ = ["build_file_service", "build_record_service", "build_table_service"]
