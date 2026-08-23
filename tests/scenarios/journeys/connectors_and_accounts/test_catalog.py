"""Connectors and accounts → finding what can be connected."""

from __future__ import annotations

from harness import capability, covers, journey, proves, scenario

pytestmark = [journey("Connectors and accounts"), capability("Find what can be connected")]


@scenario("A person browses what the platform can connect to")
@proves("PS-CONN-001")
@covers("connector.list")
async def test_the_connector_catalogue_is_browsable(world):
    alice = await world.person("priya")

    connectors = await alice.available_connectors()

    assert isinstance(connectors, list)


@scenario("A person sees what their organization has installed and connected")
@proves("PS-CONN-001")
@covers("connector.status.get")
async def test_connector_status_is_readable(world):
    alice = await world.person("priya")
    organization = alice.organization

    status = await alice.connector_status_of(organization)

    assert status is not None
