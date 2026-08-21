from __future__ import annotations

import pytest

from .helpers import cli

pytestmark = pytest.mark.e2e


@pytest.fixture
def pod_id(test_pod):
    return test_pod["id"]


def test_file_write_and_ls(backend_server, test_user, pod_id):
    write = cli(
        ["files", "write", "/notes.md", "Hello world"],
        base_url=backend_server["base_url"],
        token=test_user["token"],
        pod=pod_id,
    )
    assert write.exit_code == 0, write.stdout

    ls = cli(
        ["files", "ls", "/", "--json"],
        base_url=backend_server["base_url"],
        token=test_user["token"],
        pod=pod_id,
    )
    assert ls.exit_code == 0, ls.stdout


def test_file_cat(backend_server, test_user, pod_id):
    content = "Test content for cat."
    cli(
        ["files", "write", "/cat-test.txt", content],
        base_url=backend_server["base_url"],
        token=test_user["token"],
        pod=pod_id,
    )

    cat = cli(
        ["files", "cat", "/cat-test.txt"],
        base_url=backend_server["base_url"],
        token=test_user["token"],
        pod=pod_id,
    )
    assert cat.exit_code == 0, cat.stdout
    assert content in cat.stdout


def test_file_append(backend_server, test_user, pod_id):
    cli(
        ["files", "write", "/append-test.txt", "Line 1"],
        base_url=backend_server["base_url"],
        token=test_user["token"],
        pod=pod_id,
    )
    cli(
        ["files", "append", "/append-test.txt", "\nLine 2"],
        base_url=backend_server["base_url"],
        token=test_user["token"],
        pod=pod_id,
    )

    cat = cli(
        ["files", "cat", "/append-test.txt"],
        base_url=backend_server["base_url"],
        token=test_user["token"],
        pod=pod_id,
    )
    assert cat.exit_code == 0, cat.stdout
    assert "Line 1" in cat.stdout
    assert "Line 2" in cat.stdout


def test_file_rm(backend_server, test_user, pod_id):
    cli(
        ["files", "write", "/to-delete.txt", "Will be deleted"],
        base_url=backend_server["base_url"],
        token=test_user["token"],
        pod=pod_id,
    )

    rm = cli(
        ["files", "rm", "/to-delete.txt", "--yes"],
        base_url=backend_server["base_url"],
        token=test_user["token"],
        pod=pod_id,
    )
    assert rm.exit_code == 0, rm.stdout
