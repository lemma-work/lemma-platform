from app.modules.schedule.services.webhook_event_mapper import WebhookEventMapper


def test_normalize_slack_payload_envelope(slack_public_channel_event):
    mapper = WebhookEventMapper()

    normalized = mapper.normalize_payload("slack", slack_public_channel_event)

    assert normalized == slack_public_channel_event["payload"]


def test_extract_slack_metadata_from_fixture(slack_user_dm_event):
    mapper = WebhookEventMapper()
    normalized = mapper.normalize_payload("slack", slack_user_dm_event)

    metadata = mapper.extract_metadata("slack", normalized)

    assert metadata["team_id"] == "T0784N82P17"
    assert metadata["event_type"] == "message"
    assert metadata["channel_type"] == "im"
    assert metadata["thread_ts"] == "1766236791.639079"
    assert metadata["source_event_id"] == "1766236791.639079"


def test_extract_composio_metadata_from_fixture(composio_gmail_event):
    mapper = WebhookEventMapper()

    metadata = mapper.extract_metadata("composio", composio_gmail_event)
    event_payload = mapper.event_payload_for_source("composio", composio_gmail_event)

    assert metadata["provider_id"] == composio_gmail_event["data"]["trigger_nano_id"]
    assert metadata["event_type"] == composio_gmail_event["type"]
    assert event_payload["thread_id"] == composio_gmail_event["data"]["thread_id"]


def test_extract_composio_metadata_from_v3_payload():
    mapper = WebhookEventMapper()
    payload = {
        "type": "GOOGLECALENDAR_GOOGLE_CALENDAR_EVENT_SYNC_TRIGGER",
        "webhook_type": "composio.trigger.message",
        "metadata": {
            "trigger_slug": "GOOGLECALENDAR_GOOGLE_CALENDAR_EVENT_SYNC_TRIGGER",
            "trigger_id": "ti_v3_123",
            "connected_account_id": "ca_123",
        },
        "data": {
            "event_id": "evt_123",
        },
    }

    metadata = mapper.extract_metadata("composio", payload)
    event_payload = mapper.event_payload_for_source("composio", payload)

    assert metadata["provider_id"] == "ti_v3_123"
    assert metadata["event_type"] == "GOOGLECALENDAR_GOOGLE_CALENDAR_EVENT_SYNC_TRIGGER"
    assert metadata["connected_account_id"] == "ca_123"
    assert metadata["webhook_event_type"] == "composio.trigger.message"
    assert metadata["source_event_id"] == "evt_123"
    assert event_payload["event_id"] == "evt_123"
