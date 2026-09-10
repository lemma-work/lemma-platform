"""The callback page has to be able to say a connection is not finished.

`identity_html` and `message_html` both render a closed statement. A provider
that needs one more action -- a GitHub App that is authorized but installed
nowhere -- has nothing to render it with, and the page's own action button
cannot carry it: it goes back to Lemma, and the template's script rewrites it to
"Close this tab" whenever an opener is there.
"""

from __future__ import annotations

from app.core.api.callback_page import next_step_html, render_callback_page


def test_the_next_step_is_a_link_inside_the_body() -> None:
    html = next_step_html(
        "Authorizing does not grant repository access:",
        href="https://github.com/apps/lemmadev/installations/new",
        label="Install GitHub",
    )

    assert 'href="https://github.com/apps/lemmadev/installations/new"' in html
    assert "Install GitHub" in html
    assert "Authorizing does not grant repository access:" in html


def test_the_link_cannot_break_out_of_the_attribute() -> None:
    html = next_step_html(
        "Go here:",
        href='https://example.com/"><script>alert(1)</script>',
        label="<script>alert(2)</script>",
    )

    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_a_page_carrying_a_next_step_still_renders_as_a_success() -> None:
    response = render_callback_page(
        succeeded=True,
        app_label="GitHub",
        icon=None,
        title="GitHub needs one more step",
        body_html=next_step_html(
            "Choose which repositories it may see:",
            href="https://github.com/apps/lemmadev/installations/new",
            label="Install GitHub",
        ),
    )
    body = response.body.decode()

    assert response.status_code == 200
    # The success styling, not the failure styling: the account did connect.
    # `link--broken` is always in the stylesheet; what matters is the class the
    # template actually applied.
    assert 'class="link link--broken"' not in body
    assert "installations/new" in body
