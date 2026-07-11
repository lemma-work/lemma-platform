from app.modules.datastore.services.files.http_cache import (
    file_cache_headers,
    if_none_match_matches,
    quote_content_etag,
)


def test_content_hash_builds_strong_quoted_etag():
    digest = "ab" * 32

    assert quote_content_etag(digest.upper()) == f'"{digest}"'
    assert file_cache_headers(digest, cache_control="private, no-cache") == {
        "Cache-Control": "private, no-cache",
        "ETag": f'"{digest}"',
    }


def test_if_none_match_accepts_weak_and_list_validators_for_get():
    etag = f'"{"ab" * 32}"'

    assert if_none_match_matches(f'"other", W/{etag}', etag)
    assert if_none_match_matches("*", etag)
    assert not if_none_match_matches('"other"', etag)
    assert not if_none_match_matches(etag, None)
