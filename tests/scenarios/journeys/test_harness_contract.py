"""Guards on the suite itself.

These are the rules that make the suite worth having. They are enforced here,
in the suite, rather than in a lint script, because they are about what a
scenario *is* — and because breaking one is easy to do by accident and
impossible to spot in review once there are two hundred journeys.

They need no stack, so they run in milliseconds and fail before anything is
booted.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SUITE = Path(__file__).resolve().parents[1]
JOURNEYS = SUITE / "journeys"

#: Importing the application under test would make this a unit test wearing a
#: black-box costume: it could pass against code paths no real client can reach.
FORBIDDEN_ROOTS = {"app", "sandbox_runtime", "lemma_backend"}

#: A scenario that patches something is asserting against a system that does not
#: exist in production. The only substitutions permitted are the ones the stack
#: itself is booted with, chosen in harness/stack.py and visible to everyone.
#:
#: Deliberately *not* including a bare ``patch``: ``api.patch(...)`` is the HTTP
#: verb, and flagging it made this guard cry wolf on an honest scenario. What
#: actually indicates mocking is constructing a mock, taking the ``monkeypatch``
#: fixture, or importing the module — all three are checked below.
FORBIDDEN_NAMES = {"monkeypatch", "MagicMock", "AsyncMock", "Mock", "patch_object"}

#: Importing any of these is mocking, whatever it is later called.
FORBIDDEN_MOCK_MODULES = {"unittest.mock", "mock", "pytest_mock", "responses", "respx"}


def _python_files() -> list[Path]:
    return sorted(
        path
        for path in SUITE.rglob("*.py")
        if ".venv" not in path.parts and "__pycache__" not in path.parts
    )


def _scenario_files() -> list[Path]:
    """Journey files, excluding this one — these guards are not scenarios."""
    return sorted(
        path for path in JOURNEYS.rglob("test_*.py") if path != Path(__file__).resolve()
    )


def test_suite_is_black_box():
    """No file in the suite imports the application under test."""
    offenders: list[str] = []
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            roots: list[str] = []
            if isinstance(node, ast.Import):
                roots = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                roots = [node.module.split(".")[0]]
            for root in roots:
                if root in FORBIDDEN_ROOTS:
                    offenders.append(
                        f"{path.relative_to(SUITE)}:{node.lineno} imports {root!r}"
                    )
    assert not offenders, (
        "the scenario suite must reach Lemma only over HTTP:\n  " + "\n  ".join(offenders)
    )


def test_scenarios_do_not_mock():
    """No scenario substitutes part of the system it is testing."""
    offenders: list[str] = []
    for path in _scenario_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in FORBIDDEN_MOCK_MODULES:
                        offenders.append(
                            f"{path.relative_to(SUITE)}:{node.lineno} "
                            f"imports {alias.name!r}"
                        )
            elif (
                isinstance(node, ast.ImportFrom) and node.module in FORBIDDEN_MOCK_MODULES
            ):
                offenders.append(
                    f"{path.relative_to(SUITE)}:{node.lineno} imports from "
                    f"{node.module!r}"
                )

            name = None
            if isinstance(node, ast.Name):
                name = node.id
            elif isinstance(node, ast.arg):
                # The `monkeypatch` fixture, taken as a test argument.
                name = node.arg
            if name in FORBIDDEN_NAMES:
                offenders.append(f"{path.relative_to(SUITE)}:{node.lineno} uses {name!r}")
    assert not offenders, (
        "a scenario asserts against the real system, never a substituted one:\n  "
        + "\n  ".join(offenders)
    )


def test_scenarios_do_not_sleep():
    """No scenario waits by sleeping.

    A sleep is either too short (flaky under load) or too long (everyone pays
    for it, every run). Waiting is what the harness waiters are for, and they
    fail with what they were waiting for and what they last saw.
    """
    offenders: list[str] = []
    for path in _scenario_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                function = node.func
                if isinstance(function, ast.Attribute) and function.attr == "sleep":
                    offenders.append(f"{path.relative_to(SUITE)}:{node.lineno}")
    assert not offenders, (
        "scenarios must wait on a condition, not on the clock:\n  "
        + "\n  ".join(offenders)
    )


@pytest.mark.parametrize("path", _scenario_files(), ids=lambda p: p.name)
def test_every_scenario_declares_what_it_proves(path: Path):
    """Every test in a journey carries @scenario and @proves.

    Without both, the test is invisible to the coverage document and to the
    gates — it runs, it passes, and it contributes nothing to knowing whether
    the product does what it says.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    missing: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.name.startswith("test_"):
            continue
        decorators = {
            decorator.func.id
            for decorator in node.decorator_list
            if isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Name)
        }
        absent = {"scenario", "proves"} - decorators
        if absent:
            missing.append(f"{node.name} is missing {', '.join(sorted(absent))}")
    assert not missing, f"{path.name}:\n  " + "\n  ".join(missing)


def test_step_names_do_not_collide():
    """No two step mixins define the same verb.

    Every `steps` module is mixed into one `Person`, so a name defined twice
    silently resolves to whichever mixin comes first in the MRO — and the other
    one is simply gone, with no error at import or at call time until an
    argument happens not to match. That is a bad afternoon, so it is a guard.
    """
    import ast as _ast

    seen: dict[str, str] = {}
    clashes: list[str] = []
    for path in sorted((SUITE / "harness" / "steps").glob("*.py")):
        if path.name == "__init__.py":
            continue
        tree = _ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in _ast.walk(tree):
            if not isinstance(node, _ast.ClassDef):
                continue
            for item in node.body:
                if not isinstance(item, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                    continue
                if item.name.startswith("_"):
                    continue
                if item.name in seen and seen[item.name] != path.name:
                    clashes.append(
                        f"{item.name!r} defined in both {seen[item.name]} and {path.name}"
                    )
                seen[item.name] = path.name
    assert not clashes, "step verbs must be unique across mixins:\n  " + "\n  ".join(
        clashes
    )


#: Settings that decide where the stack's state lives. If a disposable stack
#: ever inherited one of these from a developer's `.env`, the suite would run
#: against their real database — creating, and deleting, real things.
STATE_LIVES_HERE = (
    "DATABASE_URL",
    "DATASTORE_DATABASE_URL",
    "REDIS_URL",
    "SUPERTOKENS_CORE_URL",
    "LOCAL_FILE_STORAGE_ROOT",
    "LOCAL_OBJECT_STORAGE_ROOT",
    "EMAIL_OUTPUT_DIR",
)


def test_stack_never_inherits_real_infrastructure():
    """The deployment's `.env` configures providers, never storage.

    The stack layers the backend's own `.env` underneath its settings so that a
    server configured for GitHub or Composio is configured for the live lane
    too. That ordering is what keeps it safe, and it is one careless edit from
    being wrong — so this asserts the outcome rather than the ordering.
    """
    import os

    from harness.stack import _environment

    # With inheritance on, which is when the danger exists at all.
    os.environ["SCENARIOS_USE_DEPLOYMENT_ENV"] = "1"
    try:
        stack = _environment(
            port=12345,
            database_url="postgresql://scenarios/disposable",
            redis_url="redis://scenarios/9",
            supertokens_url="http://scenarios:3567",
        )
    finally:
        os.environ.pop("SCENARIOS_USE_DEPLOYMENT_ENV", None)
    deployment = {
        name: f"postgresql://a-developers-real-machine/{name.lower()}"
        for name in STATE_LIVES_HERE
    }

    for name in STATE_LIVES_HERE:
        assert stack[name] != deployment[name], (
            f"the stack would use the deployment's {name}. A scenario run would "
            f"then create and delete records in somebody's real environment."
        )
        assert "a-developers-real-machine" not in stack[name], stack[name]

    # And the settings the live lane exists for do come through.
    assert stack["ENVIRONMENT"] == "testing"


def test_the_fast_lane_ignores_a_developers_configuration():
    """The default run is the same on every machine.

    Reading a deployment's `.env` is what the live lane needs and what the fast
    lane must not have: two scenarios about connectors and surfaces gave
    different answers on a laptop whose `.env` had Slack and Telegram
    configured, and the product was behaving correctly in both cases. A suite
    whose result depends on whose machine it runs on cannot be trusted either
    way.
    """
    from harness.stack import _deployment_settings

    assert _deployment_settings() == {}, (
        "the fast lane is reading the deployment's configuration; its results "
        "now depend on how the machine running it happens to be set up"
    )


def test_stack_decides_how_the_product_behaves():
    """A developer's `.env` cannot change which code path the suite exercises.

    Reading the deployment's configuration is what makes the live lane possible
    without a parallel set of credential names. The line it must not cross is
    behaviour: a key that lets Lemma reach GitHub is worth inheriting, a switch
    that changes how surfaces receive is not. Inherit one of those and the suite
    passes or fails depending on whose machine it runs on.
    """
    from harness.stack import DECIDED_BY_THE_STACK, _inheritable

    pretend = {name: "inherited-from-a-developer" for name in DECIDED_BY_THE_STACK}
    pretend["COMPOSIO_API_KEY"] = "worth-inheriting"

    kept = _inheritable(pretend)

    assert kept == {"COMPOSIO_API_KEY": "worth-inheriting"}, (
        f"these would be inherited and must not be: {sorted(kept)}"
    )


def test_a_target_is_vetted_before_any_scenario_can_write():
    """Nothing reaches a deployment until the suite has agreed it may.

    This suite creates real things and deletes most of them. Pointed at the
    wrong host it does that inside somebody's real workspace, and the
    organizations it leaves there are permanent — the product has no way to
    delete one. So the check goes in front of the first write, not after the
    first surprise.

    Asserted structurally because the failure it prevents is somebody quietly
    unhooking it: a `world` that no longer waits on `target`, or a `target` that
    no longer asks.
    """
    conftest = SUITE / "conftest.py"
    tree = ast.parse(conftest.read_text(encoding="utf-8"), filename=str(conftest))
    functions = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }

    assert "target" in functions, (
        "conftest has no `target` fixture, so nothing asks the deployment what "
        "it is or whether this run may write to it"
    )
    vets = any(
        isinstance(node, ast.Call)
        and getattr(node.func, "attr", getattr(node.func, "id", None))
        == "confirm_writable"
        for node in ast.walk(functions["target"])
    )
    assert vets, (
        "the `target` fixture no longer calls confirm_writable, so the suite "
        "would write to whatever host it was handed"
    )

    world_args = {argument.arg for argument in functions["world"].args.args}
    assert "target" in world_args, (
        "the `world` fixture no longer depends on `target`, so a scenario can "
        "reach a deployment that was never vetted"
    )


def test_production_is_refused_unless_somebody_said_so(monkeypatch):
    """A production target is a decision, never a default."""
    from harness.environment import (
        ALLOW_PRODUCTION,
        EXPECTED_INSTANCE,
        Deployment,
        Unreachable,
        confirm_writable,
    )

    production = Deployment(
        base_url="https://lemma.example",
        environment="production",
        llm_mode="real",
        instance_id="prod-1",
        configuration={"environment": "production", "llm_mode": "real"},
    )

    # Both guards cleared, not just the one under test. The instance check runs
    # first, so with SCENARIOS_TARGET_INSTANCE_ID set in the environment -- which
    # any run against a named target does -- this got that refusal instead and
    # failed a test about a different one. A self-test of a pure function should
    # not be able to read the ambient environment at all.
    monkeypatch.delenv(ALLOW_PRODUCTION, raising=False)
    monkeypatch.delenv(EXPECTED_INSTANCE, raising=False)
    try:
        confirm_writable(production)
    except Unreachable as refusal:
        assert ALLOW_PRODUCTION in str(refusal), refusal
    else:
        raise AssertionError(
            "the suite would have written to a production deployment without "
            "anybody saying it could"
        )


def test_a_target_pointed_somewhere_else_is_refused(monkeypatch):
    """Naming the instance is what turns a mistyped host into a stopped run."""
    from harness.environment import (
        EXPECTED_INSTANCE,
        Deployment,
        Unreachable,
        confirm_writable,
    )

    somewhere_else = Deployment(
        base_url="https://staging.example",
        environment="development",
        llm_mode="real",
        instance_id="staging-9",
        configuration={"llm_mode": "real"},
    )

    # Set through monkeypatch, which restores what was there. The `finally` this
    # replaces *popped* the variable instead -- so this test, run inside a suite
    # that had been pointed at a named instance, took that run's guard away and
    # left every scenario after it free to write anywhere.
    monkeypatch.setenv(EXPECTED_INSTANCE, "dev-scenarios-1")
    try:
        confirm_writable(somewhere_else)
    except Unreachable as refusal:
        assert "staging-9" in str(refusal), refusal
    else:
        raise AssertionError("the suite wrote to an instance it was not pointed at")


def test_a_fact_a_deployment_withholds_is_never_read_as_permission():
    """Silence is not consent, and this is where that would go wrong quietly.

    Production does not report its security posture — so every gate reads as
    absent. The scenarios that need those gates relaxed must skip there. Read
    the other way round, a missing fact would look like a satisfied one and the
    suite would try to sign people up against a deployment that never agreed.
    """
    from harness.environment import OPEN_SIGNUP, Deployment

    silent = Deployment(
        base_url="https://lemma.example",
        environment="production",
        llm_mode="real",
        instance_id=None,
        configuration={"environment": "production", "llm_mode": "real"},
    )

    assert OPEN_SIGNUP.missing_on(silent), (
        "a deployment that said nothing about its signup gates was read as "
        "having them open"
    )


def test_a_target_too_old_to_describe_itself_stops_the_run():
    """An older Lemma answers the probe without saying how it is configured.

    Treated as a stop rather than a shrug: every scenario decides from that
    answer whether it can prove anything, so a run against a target that cannot
    give one is a run whose greenness means nothing.
    """
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from harness.environment import Unreachable, describe, forget

    class OldLemma(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 — http.server's spelling
            body = json.dumps({"status": "ok", "capabilities": {}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            return

    server = HTTPServer(("127.0.0.1", 0), OldLemma)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    host, port = server.server_address[:2]
    try:
        describe(f"http://{host}:{port}")
    except Unreachable as refusal:
        assert "how it is configured" in str(refusal), refusal
    else:
        raise AssertionError("the suite ran against a target it could not read")
    finally:
        server.shutdown()
        server.server_close()
        forget()


def test_a_pod_or_an_organization_cannot_be_named_untraceably():
    """The two things whose names live somewhere that stands between runs.

    A pod's name lives in its organization and an organization's in the
    deployment, so a literal there is a 409 for whoever runs second — and
    cleanup cannot tell it from somebody's own work. Everything else a scenario
    makes is named *inside* a pod the scenario also made and deletes, and keeps
    the readable name it is actually about. That distinction came out of doing
    the migration; guessing it beforehand would have produced a rule that cried
    wolf on two thirds of the suite.

    Checked on the value that reaches the product rather than by reading the
    source, because a constant, an f-string and a name built in a helper all
    arrive the same way.
    """
    import inspect

    from harness.run import must_be_traceable
    from harness.steps.identity import IdentitySteps
    from harness.steps.pod import PodSteps

    for owner, verb, what in (
        (PodSteps, "creates_a_pod", "pod"),
        (IdentitySteps, "creates_an_organization", "organization"),
    ):
        body = inspect.getsource(getattr(owner, verb))
        assert "must_be_traceable" in body, (
            f"{verb} no longer checks that the name it is given can be traced "
            f"to a run, so a scenario can leave a {what} the next run collides "
            f"with and cleanup cannot recognise"
        )
        assert "standing" in body, (
            f"{verb} lost its `standing` escape hatch; provisioning has to be "
            f"able to make the tenant's own {what}s under their real names"
        )

    try:
        must_be_traceable("Support", what="pod")
    except AssertionError as refusal:
        assert "run.name" in str(refusal), refusal
    else:
        raise AssertionError("a literal name was accepted for a durable resource")


def test_no_scenario_scripts_the_model():
    """The agent is asked in words, never handed the tool call to make.

    The seam that allowed it is gone from the harness, and this is what stops it
    coming back — because it is genuinely tempting. Scripting a turn is the only
    way to *guarantee* an agent tries the dangerous thing, and a real model asked
    politely might not.

    What it costs is the thing this suite exists for. A scripted turn proves
    Lemma refused *that call*; it cannot prove that a person typing a sentence
    ends up refused. And against a deployment it is worse than nothing:
    `e2e_llm_mode` is `real` there, so the script is ignored in silence and the
    scenario asserts a scripted model's behaviour against a thinking one. A
    scenario in the live lane had been doing exactly that, and passing, for
    months.

    What replaced it is the product's own lever: tell the agent how to behave
    with an `instruction`, then assert on what happened.
    """
    import harness.steps.agent as agent_steps

    for gone in ("attempts", "answers", "result_of", "SCRIPT_KEY"):
        assert not hasattr(agent_steps, gone), (
            f"harness.steps.agent.{gone} is back. Scripting the model makes a "
            f"scenario a statement about the script; give the agent an "
            f"`instruction` and assert on what happens instead"
        )

    offenders = [
        f"{path.relative_to(SUITE)}:{node.lineno}"
        for path in _scenario_files()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.keyword) and node.arg == "where_the_agent"
    ]
    assert not offenders, (
        "these scenarios hand the agent its turns instead of asking it:\n  "
        + "\n  ".join(offenders)
    )


def test_a_replay_run_cannot_reach_the_real_internet():
    """The one setting the whole record/replay design rests on.

    A replay lane exists to be deterministic and credential-free. If an
    unrecorded request were *forwarded* instead of killed, a run would quietly
    reach the real provider — passing, slowly, with real side effects, and
    telling nobody. `server_replay_extra=kill` is what makes a gap in the
    recording an error rather than a silent live call.

    Asserted on the arguments the proxy is actually started with, because that
    is the thing that would be edited away.
    """
    import inspect

    from harness import egress

    started = inspect.getsource(egress.start)
    assert "server_replay_extra=kill" in started, (
        "replay no longer kills unrecorded requests, so a run with a stale "
        "cassette would reach the real internet instead of failing"
    )
    assert "stream_large_bodies" not in started, (
        "streaming is back. mitmproxy streams a response through without "
        "keeping it, so the recording replays as '200 OK (content missing)' — "
        "a response that satisfies a status assertion and contains nothing"
    )


def test_no_real_address_is_hardcoded():
    """A real mailbox is configured, never written down.

    The cast needs deliverable addresses the moment an email surface answers
    one of them — `example.com` is reserved and a reply there is a hard bounce.
    The answer is `SCENARIOS_MAILBOX`, sub-addressed per colleague.

    What must not happen is somebody committing the mailbox instead of setting
    it. This is a public repository: a real address in it is somebody's inbox,
    for as long as the history exists, and no later commit takes it back.
    """
    import re

    from harness import tenant

    reserved = re.compile(
        r"@([A-Za-z0-9.-]*\.)?(example\.(com|net|org)|example|invalid|test|localhost)$"
    )
    address = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

    found: list[str] = []
    for path in _python_files():
        for literal in address.findall(path.read_text(encoding="utf-8")):
            _, _, host = literal.partition("@")
            if not reserved.search("@" + host):
                found.append(f"{path.relative_to(SUITE)}: {literal}")

    assert not found, (
        f"these look like real email addresses, written into the suite: "
        f"{sorted(found)}. Set {tenant.MAILBOX_SETTING} instead — every "
        f"colleague is sub-addressed from it, so one mailbox covers the cast "
        f"and nothing anybody owns ends up in a public repository."
    )


def test_a_fixture_never_asserts_on_something_a_deployment_lacks():
    """What only a booted stack has must be skipped for, never asserted on.

    A deployment run owns no proxy and no stand-ins, so `stack.egress` is None
    there by design. A fixture that asserts on it turns "skipped, and here is
    why" into a stack trace — and because fixtures are shared, one line did it
    to fifty-two scenarios at once. The rule is the same one `needs()` follows:
    absent is skipped, never failed.

    Matched on `assert` against the optional parts of `Stack`, because that is
    the shape the mistake takes; a fixture is free to assert on anything a
    stack it booted itself is guaranteed to have.
    """
    optional = ("stack.egress", "stack.redis_url", "stack.database_url")
    offenders: list[str] = []
    for path in _python_files():
        if path.name == "test_harness_contract.py":
            continue
        for number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            stripped = line.strip()
            if not stripped.startswith("assert "):
                continue
            if any(name in stripped for name in optional) or (
                "is not None" in stripped and "egress" in stripped
            ):
                offenders.append(f"{path.relative_to(SUITE)}:{number}")

    assert not offenders, (
        f"these assert on something only a booted stack has: {offenders}. A "
        f"deployment run has no proxy and no stand-ins, so this has to skip "
        f"with a reason rather than error. See the `egress` fixture in "
        f"conftest.py for the shape."
    )


def test_nothing_stands_in_on_loopback_any_more():
    """No scenario may reach for a server on this machine again.

    `fake_platform.py` is gone. What replaced it is the egress proxy answering
    for real hostnames, and the difference is not stylistic: a stand-in on
    loopback could only be reached with the product's SSRF guard switched off,
    so the whole suite ran a posture no deployment uses and 43 scenarios
    skipped anywhere else.

    A new one would bring that back, quietly, which is why this is a rule
    rather than a note.
    """
    offenders = sorted(
        str(path.relative_to(SUITE))
        for path in _python_files()
        # The addon is the one place that *should* start them: it is the proxy,
        # and the proxy is what the product was pointed at.
        if path.name
        not in {"fake_upstreams.py", "egress_addon.py", "test_harness_contract.py"}
        and "start_fake_" in path.read_text(encoding="utf-8")
    )
    assert not offenders, (
        f"these start a stand-in server themselves: {offenders}. The stack owns "
        f"the fakes now — they run inside the egress proxy, which answers for "
        f"api.telegram.org and provider.scenarios.example. A scenario asks what "
        f"Lemma sent; it does not run the far end. See harness/fake_upstreams.py."
    )


def test_every_journey_runs_in_ci():
    """A journey directory nobody added to the matrix runs nowhere.

    The failure is silent and permanent: the scenarios pass locally, the CI
    check is green because it never selected them, and the promises they cover
    are reported as covered by the traceability gate — which reads the source,
    not the run. Adding a directory is the easy half; this is the half that gets
    forgotten.
    """
    import re

    workflow = (
        Path(__file__).resolve().parents[3] / ".github" / "workflows" / "scenarios.yml"
    )
    named = set(re.findall(r"journeys/([a-z_]+)", workflow.read_text()))

    on_disk = {
        directory.name
        for directory in (Path(__file__).resolve().parent).iterdir()
        if directory.is_dir()
        and not directory.name.startswith(("_", "."))
        # The live lane is deliberately elsewhere: it needs real providers and
        # runs nightly, never on a pull request.
        and directory.name != "live"
    }

    missing = on_disk - named
    assert not missing, (
        f"these journeys exist and CI never runs them: {sorted(missing)}. "
        f"Add a matrix row in .github/workflows/scenarios.yml."
    )
