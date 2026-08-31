"""Unit tests for the memory half of the runtime brief.

Two things are being protected here. The grounding — an agent must be told its
own scoped folders before it has written anything, because that is exactly the
run that needs to know where writes go. And the budget — every byte of this
lands in the system prompt of every turn, so a file nobody trimmed must not be
able to spend the whole prompt.
"""

from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.core.authorization.delegation import DEFAULT_POD_AGENT_NAME
from app.modules.agent.services import agent_memory_brief as memory_mod
from app.modules.agent.services.agent_memory_brief import (
    AgentMemoryBriefBuilder,
    invalidate_memory_brief,
    truncate_index,
)
from app.modules.datastore.contracts import (
    DatastoreAccessDeniedError,
    DatastoreFileNotFoundError,
)


class _FakeUoW:
    """The unit of work the builder is handed.

    Deliberately bare: the memory brief only reads, so it never commits, and the
    two services it passes this to are both stubbed below. Carrying `commit` and
    `rollback` stubs would be claiming this test exercises a write path it does
    not.
    """


def _uow_factory():
    class _CM:
        async def __aenter__(self):
            return _FakeUoW()

        async def __aexit__(self, *exc):
            return False

    return lambda: _CM()


class _FakeAuthzService:
    async def build_user_context(self, **kwargs):
        return object()


class _FakeFileService:
    def __init__(self, agents_md: dict[str, str]):
        self._agents_md = agents_md

    async def download_file_content_by_path(self, pod_id, path, ctx):
        content = self._agents_md.get(path)
        if content is None:
            raise DatastoreFileNotFoundError()
        return object(), content.encode("utf-8")


@pytest.fixture
def agents_md(monkeypatch):
    """A live dict of path -> AGENTS.md text, plus caching turned off."""
    files: dict[str, str] = {}
    monkeypatch.setattr(
        memory_mod, "create_authorization_service", lambda uow: _FakeAuthzService()
    )
    monkeypatch.setattr(
        memory_mod, "build_file_service", lambda uow: _FakeFileService(files)
    )
    monkeypatch.setattr(memory_mod, "_get_cache", lambda: None)
    return files


def _agent(name: str = "agent"):
    return SimpleNamespace(id=uuid4(), name=name, description=None)


async def _build(agent=None, **ids):
    return await AgentMemoryBriefBuilder(_uow_factory()).build(
        agent=agent or _agent(),
        pod_id=ids.get("pod_id", uuid4()),
        user_id=ids.get("user_id", uuid4()),
    )


async def test_scoped_folders_are_stated_before_anything_is_written(agents_md):
    """The 'where to write' grounding must show up before any AGENTS.md exists.

    It is what tells a brand-new agent where its own writes should land; waiting
    for content to exist first would make it useless on the one run that most
    needs it.
    """
    agent = _agent()

    section = await _build(agent)

    assert "## Your Memory" in section
    assert f"/memory/agents/{agent.name}/" in section
    assert f"/me/agents/{agent.name}/" in section
    assert "Pod (shared) —" not in section  # nothing written -> no content blocks


async def test_each_scope_is_rendered_under_its_own_label(agents_md):
    agents_md["/memory/AGENTS.md"] = "- pricing: see memory/pricing.md"
    agents_md["/me/AGENTS.md"] = "- prefers async updates over calls"

    section = await _build()

    assert "### Pod (shared) — `/memory/AGENTS.md`" in section
    assert "- pricing: see memory/pricing.md" in section
    assert "### This user (private) — `/me/AGENTS.md`" in section
    assert "- prefers async updates over calls" in section


async def test_lem_gets_the_same_slug_as_any_other_agent(agents_md):
    """Lem needs no special-casing: its agent.name is already 'pod_default'."""
    section = await _build(_agent(DEFAULT_POD_AGENT_NAME))

    assert "/memory/agents/pod-default/" in section
    assert "/me/agents/pod-default/" in section


async def test_an_unreadable_scope_never_takes_the_others_down(monkeypatch):
    """Not-found and access-denied are both ordinary, and neither is fatal.

    A first run raises the first on all four paths; an agent without a grant on
    `/memory` raises the second while its `/me` notes are perfectly readable.
    """

    class _PartlyExplodingFileService:
        async def download_file_content_by_path(self, pod_id, path, ctx):
            if path.startswith("/memory"):
                raise DatastoreAccessDeniedError()
            if path == "/me/AGENTS.md":
                return object(), b"- knows the private half"
            raise DatastoreFileNotFoundError()

    monkeypatch.setattr(
        memory_mod, "create_authorization_service", lambda uow: _FakeAuthzService()
    )
    monkeypatch.setattr(
        memory_mod, "build_file_service", lambda uow: _PartlyExplodingFileService()
    )
    monkeypatch.setattr(memory_mod, "_get_cache", lambda: None)

    section = await _build()

    assert "## Your Memory" in section
    assert "- knows the private half" in section
    # The header names all four canonical paths unconditionally (that's what
    # the grounding is for), so `/memory/AGENTS.md` legitimately appears there
    # even though it's unreadable here -- what must not appear is a content
    # block claiming it has something to say.
    assert "### Pod (shared)" not in section


async def test_an_oversized_index_is_cut_at_a_line_boundary_and_says_so(
    agents_md, monkeypatch
):
    """A silent cut is the one thing truncation must not be.

    The agent is the only party that can fix a bloated index, and it cannot do
    that from content that simply stops.
    """
    monkeypatch.setattr(memory_mod.agent_settings, "agent_memory_index_max_chars", 120)
    agents_md["/memory/AGENTS.md"] = "\n".join(f"- topic {n}" for n in range(60))

    section = await _build()
    rendered = section[section.index("### Pod (shared)") :]

    assert "- topic 0" in rendered
    assert "- topic 59" not in rendered
    assert "truncated:" in rendered
    assert "`/memory/AGENTS.md`" in rendered
    # Cut between lines, never through one.
    assert "\n… [" in rendered
    assert rendered.split("\n… [")[0].endswith(tuple(f"- topic {n}" for n in range(60)))


async def test_the_whole_section_stays_within_its_budget(agents_md, monkeypatch):
    monkeypatch.setattr(memory_mod.agent_settings, "agent_memory_index_max_chars", 200)
    monkeypatch.setattr(
        memory_mod.agent_settings, "agent_memory_section_max_chars", 300
    )
    for path in (
        "/memory/AGENTS.md",
        "/memory/agents/agent/AGENTS.md",
        "/me/AGENTS.md",
        "/me/agents/agent/AGENTS.md",
    ):
        agents_md[path] = "\n".join(f"- {path} line {n}" for n in range(50))

    section = await _build()

    # The grounding header is unconditional; the budget bounds everything the
    # four scopes contribute after it, headings included.
    content = section[section.index("###") :]
    assert len(content) <= 300


async def test_the_budget_is_spent_on_the_narrowest_scope_first(agents_md, monkeypatch):
    """When there is not room for everything, this user's notes win.

    The pod-shared index is the one every agent writes to and therefore the one
    that grows; the private per-agent note is the likeliest to be the answer to
    the question actually being asked.
    """
    private_path = "/me/agents/agent/AGENTS.md"
    private_heading = f"\n### agent + this user (private) — `{private_path}`\n"
    agents_md[private_path] = "- private fact"
    agents_md["/memory/AGENTS.md"] = "- shared fact"
    monkeypatch.setattr(memory_mod.agent_settings, "agent_memory_index_max_chars", 200)
    # Room for the private scope and a few characters over -- not enough for the
    # shared scope's heading, let alone its content.
    monkeypatch.setattr(
        memory_mod.agent_settings,
        "agent_memory_section_max_chars",
        len(private_heading) + len("- private fact") + 5,
    )

    section = await _build()

    assert "- private fact" in section
    assert "- shared fact" not in section


async def test_a_zero_budget_renders_the_grounding_and_no_content(
    agents_md, monkeypatch
):
    monkeypatch.setattr(memory_mod.agent_settings, "agent_memory_section_max_chars", 0)
    agents_md["/memory/AGENTS.md"] = "- pricing"

    section = await _build()

    assert "## Your Memory" in section
    assert "- pricing" not in section


def test_truncate_index_leaves_a_short_file_alone():
    assert truncate_index("- one\n- two", limit=100, path="/x") == "- one\n- two"


def test_truncate_index_reports_exactly_what_it_dropped():
    """The count in the marker has to match reality, not the reservation.

    Room for the marker is reserved using the longest one the file could
    produce, so the marker finally written is usually for fewer lines than were
    budgeted for. It must still say how many actually went.
    """
    text = "\n".join(f"- line {n}" for n in range(10))

    trimmed = truncate_index(text, limit=80, path="/x")

    kept, _, marker = trimmed.partition("\n… [")
    assert text.startswith(kept)  # a prefix of the file, cut at a line boundary
    assert marker
    dropped = len(text.splitlines()) - len(kept.splitlines())
    assert f"{dropped} more lines" in marker
    assert len(trimmed) <= 80


def test_truncate_index_is_honest_about_cutting_inside_one_long_line():
    """Reporting '0 more lines' would read as a bug, and dropping the only line
    would leave nothing at all."""
    trimmed = truncate_index("x" * 400, limit=120, path="/x")

    assert trimmed.startswith("x" * 20)
    assert "mid-line" in trimmed


def test_truncate_index_never_returns_more_than_its_limit():
    """The marker is charged to the limit, not added on top of it -- otherwise
    the section budget that calls this quietly overruns on every scope."""
    for limit in (60, 90, 120, 400):
        for text in ("x" * 1000, "\n".join(f"- line {n}" for n in range(200))):
            assert (
                len(truncate_index(text, limit=limit, path="/memory/AGENTS.md"))
                <= limit
            )


def test_truncate_index_drops_a_scope_it_cannot_even_mark():
    """A heading over a marker over nothing is worse than saying nothing."""
    assert truncate_index("x" * 400, limit=10, path="/memory/AGENTS.md") == ""


class _RecordingCache:
    def __init__(self):
        self.deleted: list[str] = []

    async def delete_prefix(self, sub_prefix: str) -> None:
        self.deleted.append(sub_prefix)


@pytest.fixture
def recording_cache(monkeypatch):
    cache = _RecordingCache()
    monkeypatch.setattr(memory_mod, "_get_cache", lambda: cache)
    return cache


_POD = UUID("00000000-0000-0000-0000-0000000000aa")
_USER = UUID("00000000-0000-0000-0000-0000000000bb")


async def test_a_shared_write_invalidates_the_whole_pod(recording_cache):
    """Pod-shared memory is in every member's brief, so every member's entry
    has to go — which is the reason the cache key starts with the pod."""
    await invalidate_memory_brief(pod_id=_POD, path="/memory/AGENTS.md", user_id=_USER)

    assert recording_cache.deleted == [f"{_POD}:"]


async def test_a_private_write_invalidates_only_that_user(recording_cache):
    await invalidate_memory_brief(pod_id=_POD, path="/me/AGENTS.md", user_id=_USER)

    assert recording_cache.deleted == [f"{_POD}:{_USER}:"]


async def test_the_storage_form_of_a_private_path_names_its_own_owner(
    recording_cache,
):
    """Tools hand over `/{owner}/...`, and the owner is not always the actor —
    so the path is believed over whoever happened to make the write."""
    other = uuid4()

    await invalidate_memory_brief(
        pod_id=_POD, path=f"/{_USER}/AGENTS.md", user_id=other
    )

    assert recording_cache.deleted == [f"{_POD}:{_USER}:"]


async def test_an_unrelated_write_invalidates_nothing(recording_cache):
    await invalidate_memory_brief(
        pod_id=_POD, path="/knowledge/policy.pdf", user_id=_USER
    )

    assert recording_cache.deleted == []


class TestAllFourIndexesActuallyArrive:
    """The header promises four indexes. It has to be telling the truth.

    Four indexes at the 2000-char per-index cap could not fit a 6000-char
    section, and the budget was spent narrowest-scope-first, so the scope that
    fell off the end was always the same one: `/memory/AGENTS.md`, the
    pod-shared index every agent writes to and therefore the one most likely to
    have grown. It disappeared with no heading and no marker, directly beneath a
    header telling the agent that all four had been read in together and there
    was "nothing to pick between".
    """

    def _four_realistic_indexes(self, agents_md, agent_name: str) -> None:
        for path in (
            f"/me/agents/{agent_name}/AGENTS.md",
            "/me/AGENTS.md",
            f"/memory/agents/{agent_name}/AGENTS.md",
            "/memory/AGENTS.md",
        ):
            # Over the per-index cap, which is the case that used to lose one:
            # four indexes each spending their full 2000 characters could not
            # fit a 6000-character section.
            body = "\n".join(f"- {path} entry {n}" for n in range(120))
            agents_md[path] = body

    async def test_the_pod_shared_index_is_not_the_one_that_disappears(
        self, agents_md
    ) -> None:
        agent = _agent()
        self._four_realistic_indexes(agents_md, agent.name)

        section = await _build(agent=agent)

        assert "/memory/AGENTS.md entry 0" in section

    async def test_every_scope_reaches_the_prompt(self, agents_md) -> None:
        agent = _agent()
        self._four_realistic_indexes(agents_md, agent.name)

        section = await _build(agent=agent)

        for path in (
            f"/me/agents/{agent.name}/AGENTS.md",
            "/me/AGENTS.md",
            f"/memory/agents/{agent.name}/AGENTS.md",
            "/memory/AGENTS.md",
        ):
            # The rendered block heading, not a bare mention: the header above
            # names all four paths whether or not their contents arrived.
            assert f"— `{path}`\n" in section, f"{path} never reached the prompt"

    async def test_a_scope_that_cannot_fit_says_so(self, agents_md, monkeypatch):
        """Silence reads as 'this scope holds nothing', so the agent stops
        looking instead of opening the file."""
        agent = _agent()
        self._four_realistic_indexes(agents_md, agent.name)
        monkeypatch.setattr(
            memory_mod.agent_settings, "agent_memory_index_max_chars", 400
        )
        monkeypatch.setattr(
            memory_mod.agent_settings, "agent_memory_section_max_chars", 700
        )

        section = await _build(agent=agent)

        assert "Not shown this turn" in section
        assert "/memory/AGENTS.md" in section

    async def test_the_section_still_respects_its_budget(self, agents_md) -> None:
        agent = _agent()
        self._four_realistic_indexes(agents_md, agent.name)

        section = await _build(agent=agent)

        budget = memory_mod.agent_settings.agent_memory_section_max_chars
        indexes_start = section.index("### ")
        assert len(section[indexes_start:]) <= budget
