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
        "the scenario suite must reach Lemma only over HTTP:\n  "
        + "\n  ".join(offenders)
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
            elif isinstance(node, ast.ImportFrom) and node.module in FORBIDDEN_MOCK_MODULES:
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
                offenders.append(
                    f"{path.relative_to(SUITE)}:{node.lineno} uses {name!r}"
                )
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
            if isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Name)
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
                        f"{item.name!r} defined in both {seen[item.name]} and "
                        f"{path.name}"
                    )
                seen[item.name] = path.name
    assert not clashes, (
        "step verbs must be unique across mixins:\n  " + "\n  ".join(clashes)
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


def test_production_is_refused_unless_somebody_said_so():
    """A production target is a decision, never a default."""
    import os

    from harness.environment import ALLOW_PRODUCTION, Deployment, Unreachable
    from harness.environment import confirm_writable

    production = Deployment(
        base_url="https://lemma.example",
        environment="production",
        llm_mode="real",
        instance_id="prod-1",
        configuration={"environment": "production", "llm_mode": "real"},
    )

    os.environ.pop(ALLOW_PRODUCTION, None)
    try:
        confirm_writable(production)
    except Unreachable as refusal:
        assert ALLOW_PRODUCTION in str(refusal), refusal
    else:
        raise AssertionError(
            "the suite would have written to a production deployment without "
            "anybody saying it could"
        )


def test_a_target_pointed_somewhere_else_is_refused():
    """Naming the instance is what turns a mistyped host into a stopped run."""
    import os

    from harness.environment import EXPECTED_INSTANCE, Deployment, Unreachable
    from harness.environment import confirm_writable

    somewhere_else = Deployment(
        base_url="https://staging.example",
        environment="development",
        llm_mode="real",
        instance_id="staging-9",
        configuration={"llm_mode": "real"},
    )

    os.environ[EXPECTED_INSTANCE] = "dev-scenarios-1"
    try:
        confirm_writable(somewhere_else)
    except Unreachable as refusal:
        assert "staging-9" in str(refusal), refusal
    else:
        raise AssertionError("the suite wrote to an instance it was not pointed at")
    finally:
        os.environ.pop(EXPECTED_INSTANCE, None)


def test_a_fact_a_deployment_withholds_is_never_read_as_permission():
    """Silence is not consent, and this is where that would go wrong quietly.

    Production does not report its security posture — so every gate reads as
    absent. The scenarios that need those gates relaxed must skip there. Read
    the other way round, a missing fact would look like a satisfied one and the
    suite would try to sign people up against a deployment that never agreed.
    """
    from harness.environment import LOOPBACK_REACHABLE, OPEN_SIGNUP, Deployment

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
    assert LOOPBACK_REACHABLE.missing_on(silent), (
        "a deployment that said nothing was read as able to call back to this "
        "machine"
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
        Path(__file__).resolve().parents[3]
        / ".github"
        / "workflows"
        / "scenarios.yml"
    )
    named = set(re.findall(r"journeys/([a-z_]+)", workflow.read_text()))

    on_disk = {
        directory.name
        for directory in (Path(__file__).resolve().parent).iterdir()
        if directory.is_dir() and not directory.name.startswith(("_", "."))
        # The live lane is deliberately elsewhere: it needs real providers and
        # runs nightly, never on a pull request.
        and directory.name != "live"
    }

    missing = on_disk - named
    assert not missing, (
        f"these journeys exist and CI never runs them: {sorted(missing)}. "
        f"Add a matrix row in .github/workflows/scenarios.yml."
    )
