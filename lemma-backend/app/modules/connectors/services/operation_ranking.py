"""How closely a discovery query matches one operation.

Split out of `ConnectorOperationService`, which was over its size ceiling and
holds none of the state this needs: the score is a pure function of a query and
an operation row, and it is the same score whether the row came from the
catalog or from an install's own discovered set.

The repository already ranks in SQL. This runs again on the merged result --
which SQL cannot rank, because half of it comes from another table -- and it is
what the caller sees as `relevance_score`.
"""

from __future__ import annotations


def normalized_operation_name(operation_name: str) -> str:
    """The form operation names are compared in: trimmed and lowercased."""
    return operation_name.strip().lower()


def operation_relevance_score(operation: object, query: str | None) -> float | None:
    """`0.0`-`1.0` for how well `operation` answers `query`, or None if there
    was no query to answer."""
    if not query:
        return None

    normalized_query = " ".join(
        query.replace("_", " ").replace("-", " ").replace("/", " ").lower().split()
    )
    if not normalized_query:
        return None

    tokens = normalized_query.split()
    name = str(getattr(operation, "name", "") or "").lower()
    provider_name = str(getattr(operation, "provider_operation_name", "") or "").lower()
    display_name = str(getattr(operation, "display_name", "") or "").lower()
    description = str(getattr(operation, "description", "") or "").lower()
    search_document = str(getattr(operation, "search_document", "") or "").lower()
    compact_names = {
        name,
        provider_name,
        display_name,
        name.replace("_", " "),
        provider_name.replace("_", " "),
    }
    name_text = " ".join(compact_names)
    all_text = " ".join([name_text, description, search_document])

    score = 0.0
    if normalized_query in compact_names:
        score = max(score, 1.0)
    if normalized_query and normalized_query in name_text:
        score = max(score, 0.95)
    if tokens:
        name_matches = sum(1 for token in tokens if token in name_text)
        all_matches = sum(1 for token in tokens if token in all_text)
        score = max(score, 0.85 * (name_matches / len(tokens)))
        score = max(score, 0.7 * (all_matches / len(tokens)))
    return round(score, 3)
