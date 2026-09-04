"""A deployment can be inspected and restored without losing its source."""

from io import BytesIO
from zipfile import ZipFile

from harness import capability, covers, journey, proves, scenario

pytestmark = [journey("Packaging and reuse"), capability("Give work an interface")]


def _site(message: str) -> bytes:
    result = BytesIO()
    with ZipFile(result, "w") as archive:
        archive.writestr(
            "index.html", f"<!doctype html><title>Orders</title><p>{message}</p>"
        )
    return result.getvalue()


@scenario("An editor restores an earlier app deployment and its source")
@proves("PS-PACK-030")
@covers(
    "app.create",
    "app.bundle.upload",
    "app.release.list",
    "app.release.promote",
    "app.source.archive.get",
)
async def test_an_earlier_deployment_can_be_restored(world, run):
    owner = await world.person("daniel")
    pod = await owner.creates_a_pod(named=run.name("versions"))
    app = await owner.creates_an_app(in_pod=pod)
    base = f"/pods/{pod['id']}/apps/{app['name']}"
    original = _site("Original orders")
    for site in (original, _site("Revised orders")):
        await owner.api.expect(
            "POST",
            base + "/bundle",
            status=200,
            files={
                "source_archive": ("source.zip", site, "application/zip"),
                "dist_archive": ("dist.zip", site, "application/zip"),
            },
        )
    history = await owner.api.get(base + "/releases")
    assert [item["release_number"] for item in history["items"]] == [2, 1]
    assert history["items"][0]["is_live"]
    await owner.api.post(base + "/releases/v1/promote")
    restored = await owner.api.get(base + "/releases")
    assert (
        next(item for item in restored["items"] if item["is_live"])["release_number"] == 1
    )
    source = await owner.api.call("GET", base + "/source/archive")
    assert source.status_code == 200
    assert source.content == original
