from __future__ import annotations

from app.modules.datastore.infrastructure.markdown_images import (
    rewrite_image_references,
)


def test_rewrites_pathful_refs_to_known_basenames():
    md = '![a](images/fig1.png)\n\n<img src="./assets/fig2.png">'
    out = rewrite_image_references(md, {"fig1.png", "fig2.png"})
    assert "![a](fig1.png)" in out
    assert 'src="fig2.png"' in out


def test_leaves_unknown_and_external_refs_untouched():
    md = "![x](https://example.com/remote.png)\n\n![y](images/unknown.png)"
    out = rewrite_image_references(md, {"fig1.png"})
    assert out == md  # neither basename is a known artifact


def test_strips_query_and_fragment_when_matching():
    md = "![t](images/fig1.png?v=2#frag)"
    out = rewrite_image_references(md, {"fig1.png"})
    assert "![t](fig1.png)" in out


def test_noop_without_image_names():
    md = "![a](fig1.png)"
    assert rewrite_image_references(md, set()) == md
