"""The generated names must match the frontend's, character for character.

Two implementations of "what is this workspace called" exist while the web
onboarding is moved onto this one, and they name the same person's workspace.
The expected values below were produced by running the TypeScript's own
algorithm, not by reading it -- a port that merely looks right is how the two
quietly disagree.
"""

from __future__ import annotations

import pytest

from app.modules.identity.domain.workspace_names import (
    first_name_from_address,
    first_pod_name,
    generated_organization_name,
    organization_lemma_name,
    organization_name_candidate,
    organization_name_from_work_domain,
    personal_organization_name,
    to_title_case,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "seed,attempt,expected",
    [
        ("ada@gmail.com", 0, "Golden Summit"),
        ("ada@gmail.com", 1, "Open Atlas"),
        ("ada@gmail.com", 7, "North Grove"),
        # Case and surrounding space are normalised away before hashing.
        ("BOB@Hotmail.COM ", 0, "Golden Compass"),
        # An empty seed falls back to "lemma" rather than hashing nothing.
        ("", 0, "North Forge"),
        ("z", 0, "North Signal"),
        ("a.very.long.person@some-consumer-provider.example", 3, "Olive Lantern"),
        # Non-ASCII: the hash reads UTF-16 units, as JavaScript does.
        ("josé@outlook.com", 0, "Copper Grove"),
    ],
)
def test_generated_names_match_the_frontends_output(seed, attempt, expected) -> None:
    assert generated_organization_name(seed, attempt) == expected


def test_the_same_address_always_gets_the_same_name() -> None:
    """Retrying gets the workspace you half-made, not a second one beside it."""
    assert generated_organization_name("ada@gmail.com") == generated_organization_name(
        "ada@gmail.com"
    )


@pytest.mark.parametrize(
    "domain,expected",
    [
        ("acme.com", "Acme"),
        ("@acme.com", "Acme"),
        # A registry second level names nobody: acme.co.uk is Acme, not Co.
        ("acme.co.uk", "Acme"),
        ("shop.acme.com", "Acme"),
        ("north-wind.io", "North Wind"),
        # A team's subdomain is still the company.
        ("eng.acme.com", "Acme"),
        ("eng.acme.co.uk", "Acme"),
        ("acme.io", "Acme"),
        # Nothing to name a company after.
        ("localhost", None),
    ],
)
def test_a_work_domain_names_the_company(domain, expected) -> None:
    assert organization_name_from_work_domain(domain) == expected


def test_a_taken_company_name_falls_back_before_it_gives_up() -> None:
    """The company, then the domain, then numbered, then something unique."""
    kwargs = {"email": "ada@acme.com", "work_domain": "acme.com"}
    assert organization_name_candidate(**kwargs, attempt=0) == "Acme"
    assert organization_name_candidate(**kwargs, attempt=1) == "acme.com"
    assert organization_name_candidate(**kwargs, attempt=2) == "Acme 2"
    # Past the company-shaped attempts, a generated name no company collides with.
    assert " " in organization_name_candidate(**kwargs, attempt=10)
    assert not organization_name_candidate(**kwargs, attempt=10).startswith("Acme")


def test_a_personal_address_is_named_after_the_person_it_spells() -> None:
    """Never a generated phrase: a workspace of one says whose it is."""
    assert organization_name_candidate(email="ada@gmail.com") == "Ada's workspace"
    assert organization_name_candidate(email="ada@gmail.com", attempt=3) == (
        "Ada's workspace"
    )
    assert organization_name_candidate(email="x9@gmail.com") == "My workspace"


@pytest.mark.parametrize(
    "email,expected",
    [
        ("ada@gmail.com", "Ada"),
        ("ADA@gmail.com", "Ada"),
        ("ada.lovelace@gmail.com", "Ada"),
        ("ada_lovelace@gmail.com", "Ada"),
        ("ada-lovelace@gmail.com", "Ada"),
        ("ada+work@gmail.com", "Ada"),
        # Unicode letters are letters.
        ("josé.garcía@gmail.com", "José"),
        ("émile@gmail.com", "Émile"),
        # A mailbox is not a name: digits, one letter, or nothing at all.
        ("ada0196+99@gmail.com", None),
        ("a@gmail.com", None),
        ("a.lovelace@gmail.com", None),
        ("1ada@gmail.com", None),
        ("+ada@gmail.com", None),
        ("@gmail.com", None),
        ("not-an-address", None),
        ("", None),
        (None, None),
    ],
)
def test_an_address_spells_a_first_name_only_when_it_reads_as_one(
    email, expected
) -> None:
    assert first_name_from_address(email) == expected


@pytest.mark.parametrize(
    "full_name,email,expected",
    [
        ("Ada Lovelace", "x9@gmail.com", "Ada's workspace"),
        # The first word, however it is spaced.
        ("  Ada   Lovelace ", None, "Ada's workspace"),
        # A name given wins over the address.
        ("Grace", "ada@gmail.com", "Grace's workspace"),
        (None, "grace.hopper@gmail.com", "Grace's workspace"),
        ("   ", "grace@gmail.com", "Grace's workspace"),
        (None, "x9@gmail.com", "My workspace"),
        (None, None, "My workspace"),
    ],
)
def test_a_workspace_of_one_is_named_after_its_person(
    full_name, email, expected
) -> None:
    assert personal_organization_name(full_name, email) == expected


@pytest.mark.parametrize(
    "organization_name,team,expected",
    [
        ("Acme", True, "Acme"),
        ("North Wind", True, "North Wind"),
        # Folded to what a pod name allows.
        ("Café Ünïcode", True, "Cafe Unicode"),
        ("Ada's workspace", True, "Adas workspace"),
        ("-Acme-", True, "Acme"),
        # Nothing a pod may be called survives the fold.
        ("東京", True, "Everyone"),
        # A workspace of one has one Lemma, and it is theirs.
        ("Ada's workspace", False, "Personal"),
        ("Acme", False, "Personal"),
    ],
)
def test_an_organizations_lemma_is_called_what_it_is(
    organization_name, team, expected
) -> None:
    assert organization_lemma_name(organization_name, team=team) == expected


def test_a_personal_workspace_is_named_after_its_person() -> None:
    """Never "Personal 5": the name says whose it is."""
    assert (
        organization_name_candidate(email="ada@gmail.com", full_name="Ada Lovelace")
        == "Ada's workspace"
    )
    # A company still names a company's workspace.
    assert (
        organization_name_candidate(
            email="ada@acme.com", work_domain="acme.com", full_name="Ada Lovelace"
        )
        == "Acme"
    )


@pytest.mark.parametrize(
    "full_name,expected",
    [
        ("Ada Lovelace", "Ada Pod"),
        ("ada", "ada Pod"),
        (None, "Personal Pod"),
        ("", "Personal Pod"),
        # Folded to what the server accepts, rather than rejected on create.
        ("José", "Jose Pod"),
        ("O'Brien", "OBrien Pod"),
        # Nothing usable survives the fold.
        ("🙂", "Personal Pod"),
    ],
)
def test_a_first_pod_is_named_after_its_person(full_name, expected) -> None:
    assert first_pod_name(full_name) == expected


def test_title_case_leaves_joining_words_alone_after_the_first() -> None:
    assert to_title_case("bank of england") == "Bank of England"
    assert to_title_case("of course") == "Of Course"
