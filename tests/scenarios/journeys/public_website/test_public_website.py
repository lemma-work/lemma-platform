"""Visitors can discover Lemma and keep following their existing links."""

from harness import capability, journey, proves, scenario
from harness.public_site import PublicWebsite

pytest_plugins = ["harness.public_site"]

pytestmark = [journey("Getting started"), capability("Explore the public website")]


@scenario("A visitor can read every indexed guide without signing in")
@proves("PS-ONB-060")
def test_public_guides_and_company_pages(public_site: PublicWebsite) -> None:
    public_site.reads_public_pages()


@scenario("An AI reader can request Markdown at the same documentation address")
@proves("PS-ONB-061")
def test_machine_readable_site(public_site: PublicWebsite) -> None:
    public_site.reads_machine_readable_content()


@scenario("An existing link still reaches its sign-in page or workspace resource")
@proves("PS-ONB-062")
def test_existing_links(public_site: PublicWebsite) -> None:
    public_site.follows_existing_links()
