"""Operation naming for discovered installs.

Normalizing a provider's tool name is lossy, and the losses collide. Two tools
that differ only in punctuation or case reduce to the same slug, and both rows
then hit the install's unique index. In the delete-then-insert version of
re-discovery that aborted the transaction *after* the delete, so a server with
one unlucky pair of tool names left the install with no operations at all.
"""

from __future__ import annotations

import pytest

from app.modules.connectors.services.discovery.base import (
    assign_unique_names,
    normalize_operation_name,
)

pytestmark = pytest.mark.unit


class TestNormalization:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Get User", "get_user"),
            ("get-user", "get_user"),
            ("getUser", "getuser"),
            ("  spaced  out  ", "spaced_out"),
            ("weird!!chars@@here", "weird_chars_here"),
            ("__leading_and_trailing__", "leading_and_trailing"),
        ],
    )
    def test_names_reduce_to_a_stable_slug(self, raw, expected):
        assert normalize_operation_name(raw) == expected

    def test_an_empty_name_still_produces_something_addressable(self):
        assert normalize_operation_name("") == "operation"
        assert normalize_operation_name("!!!") == "operation"


class TestCollisions:
    def test_two_names_that_normalize_alike_are_disambiguated(self):
        # The exact case that used to wipe an install's operation set.
        assert assign_unique_names(["Get User", "get_user"]) == [
            "get_user",
            "get_user_2",
        ]

    def test_three_way_collisions_keep_counting(self):
        assert assign_unique_names(["Get User", "get-user", "GET USER"]) == [
            "get_user",
            "get_user_2",
            "get_user_3",
        ]

    def test_unrelated_names_are_untouched(self):
        assert assign_unique_names(["search", "create_issue"]) == [
            "search",
            "create_issue",
        ]

    def test_every_assigned_name_is_unique(self):
        # The property the unique index actually depends on.
        raw = ["A b", "a-b", "a_b", "other", "OTHER", "other!"]
        assigned = assign_unique_names(raw)
        assert len(set(assigned)) == len(assigned)

    def test_assignment_is_order_stable_across_refreshes(self):
        # A tool keeps its name as long as the server returns its list in the
        # same order, so agents and workflows bound to it keep working.
        raw = ["Get User", "get_user", "search"]
        assert assign_unique_names(raw) == assign_unique_names(raw)

    def test_it_preserves_input_length(self):
        raw = ["a", "A", "a!", "b"]
        assert len(assign_unique_names(raw)) == len(raw)
