"""Unit tests for the advisory app-HTML linter (unified browser-SDK contract)."""

from app.core.widget_html_validation import (
    lint_app_html,
    validate_widget_html,
)

UNIFIED_OK = """
<div id="root">loading</div>
<script>
  (function () {
    var cfg = window.__LEMMA_CONFIG__ || {};
    var base = (cfg.apiUrl || window.location.origin).replace(/\\/$/, "");
    var s = document.createElement("script");
    s.src = base + "/public/sdk/lemma-client.js";
    s.onload = boot;
    document.head.appendChild(s);
  })();
  function boot() {
    const client = new window.LemmaClient.LemmaClient();
    client.records.list("tickets", { limit: 50 });
  }
</script>
"""


def test_unified_contract_is_clean():
    assert lint_app_html(UNIFIED_OK) == []


def test_flags_runtime_babel():
    html = '<script src="https://unpkg.com/@babel/standalone/babel.min.js"></script>'
    issues = lint_app_html(html)
    assert any("Babel" in i for i in issues)


def test_flags_retired_pod_client_sdk():
    html = (
        '<script type="module">import { LemmaPodClient } from "@lemma/pod-client";'
        "</script>"
    )
    issues = lint_app_html(html)
    assert any("@lemma/pod-client" in i for i in issues)


def test_flags_retired_pod_client_script_tag():
    html = '<script src="/public/sdk/pod-client.js"></script>'
    issues = lint_app_html(html)
    assert any("pod-client.js" in i for i in issues)


def test_flags_namespace_object_used_as_constructor():
    html = "<script>const c = new window.LemmaClient({ podId: 'x' });</script>"
    issues = lint_app_html(html)
    assert any("namespace object" in i for i in issues)


def test_does_not_flag_correct_double_constructor():
    html = "<script>const c = new window.LemmaClient.LemmaClient();</script>"
    assert lint_app_html(html) == []


def test_flags_hardcoded_absolute_sdk_host():
    # An app's own subdomain does not serve /public/sdk — build from cfg.apiUrl.
    html = (
        '<script src="https://crm-app.apps.lemma.work/public/sdk/lemma-client.js">'
        "</script>"
    )
    issues = lint_app_html(html)
    assert any("absolute host" in i for i in issues)


def test_flags_dynamic_hardcoded_absolute_sdk_host():
    issues = lint_app_html(
        '<script>sdk.src = "https://api.lemma.test/public/sdk/lemma-client.js"</script>'
    )
    assert any("absolute host" in i for i in issues)


def test_flags_relative_sdk_path():
    # A relative src 404s on app subdomains (only the API origin serves the SDK).
    html = '<script src="/public/sdk/lemma-client.js"></script>'
    issues = lint_app_html(html)
    assert any("relative" in i for i in issues)


def test_flags_dynamic_relative_sdk_path():
    issues = lint_app_html('<script>sdk.src = "/public/sdk/lemma-client.js"</script>')
    assert any("relative" in i for i in issues)


def test_does_not_flag_config_derived_sdk_loader():
    assert lint_app_html(UNIFIED_OK) == []


def test_flags_hardcoded_pod_id():
    html = (
        "<script>const c = new window.LemmaClient.LemmaClient("
        "{ podId: '019ebadc-d86a-7424-9221-e3424f05b1a6' });</script>"
    )
    issues = lint_app_html(html)
    assert any("Hardcoded pod id" in i for i in issues)


def test_widget_contract_accepts_static_fragment():
    assert validate_widget_html("<div class='card'><p>7 open</p></div>") == []


def test_widget_contract_accepts_inline_svg_icon_inside_html():
    """Only an SVG *root* is an image; icons inside a fragment stay legal."""
    html = (
        "<div class='row'><svg viewBox='0 0 8 8'><circle cx='4' cy='4' r='4'/>"
        "</svg><span>Online</span></div>"
    )
    assert validate_widget_html(html) == []


def test_widget_contract_rejects_standalone_svg():
    errors = validate_widget_html("<svg><circle cx='5' cy='5' r='5'/></svg>")
    assert any("not a standalone SVG" in e for e in errors)
    assert any('type="FILE"' in e for e in errors)


def test_widget_contract_rejects_svg_root_behind_a_comment():
    errors = validate_widget_html("<!-- chart --><svg><rect width='4'/></svg>")
    assert any("not a standalone SVG" in e for e in errors)


def test_widget_contract_rejects_base64_content():
    """A base64 blob trips no markup rule, so it needs its own check."""
    errors = validate_widget_html("PGRpdiBzdHlsZT0iY29sb3I6cmVkIj5oaTwvZGl2Pg==")
    assert any("no element tag found" in e for e in errors)
    assert any("base64" in e for e in errors)


def test_widget_contract_survives_comment_bomb_in_linear_time():
    """The SVG-root check must not backtrack.

    Folding the leading-comment skip into the pattern
    (``(?:<!--.*?-->\\s*)*<svg``) backtracks exponentially on input that opens a
    comment and never reaches an ``<svg>``. Widget content comes from the agent,
    so that input is reachable; 20k repetitions would not return this decade.
    """
    import time

    bomb = "<!--" + "--><!--" * 20_000
    started = time.perf_counter()
    validate_widget_html(bomb)
    assert time.perf_counter() - started < 1.0


def test_widget_contract_rejects_unsubstituted_placeholder_text():
    errors = validate_widget_html("GRLk5IzpCh72PD... [full HTML below]")
    assert any("no element tag found" in e for e in errors)


def test_widget_contract_accepts_portable_sdk_fragment():
    assert validate_widget_html(UNIFIED_OK) == []


def test_widget_contract_accepts_direct_runtime_config_api_url():
    html = UNIFIED_OK.replace("cfg.apiUrl", "window.__LEMMA_CONFIG__.apiUrl")
    assert validate_widget_html(html) == []


def test_widget_contract_accepts_bracket_runtime_config_api_url():
    html = UNIFIED_OK.replace("cfg.apiUrl", 'cfg["apiUrl"]')
    assert validate_widget_html(html) == []


def test_widget_contract_accepts_destructured_runtime_config_api_url():
    html = UNIFIED_OK.replace(
        "var cfg = window.__LEMMA_CONFIG__ || {};",
        "const { apiUrl } = window.__LEMMA_CONFIG__ || {};",
    ).replace("cfg.apiUrl", "apiUrl")
    assert validate_widget_html(html) == []


def test_widget_contract_accepts_aliased_runtime_config_api_url():
    assert "var cfg = window.__LEMMA_CONFIG__" in UNIFIED_OK
    assert "cfg.apiUrl" in UNIFIED_OK
    assert validate_widget_html(UNIFIED_OK) == []


def test_widget_contract_rejects_full_document():
    issues = validate_widget_html("<!doctype html><html><body>x</body></html>")
    assert any("fragment" in issue for issue in issues)


def test_widget_contract_rejects_css_outside_style_tag():
    # CSS authored without a <style> wrapper renders as literal text in the
    # served document. Regression: a model emitted naked rules before markup.
    html = (
        "body{font-family:var(--lemma-widget-font);margin:0}"
        ".card{background:var(--lemma-widget-surface);padding:20px}\n"
        '<div class="card"><h1>Tool run</h1></div>'
    )
    issues = validate_widget_html(html)
    assert any("outside any <style> tag" in issue for issue in issues)


def test_widget_contract_accepts_css_inside_style_tag():
    html = (
        "<style>.card{color:var(--lemma-widget-text);padding:10px}</style>"
        '<div class="card">hi</div>'
    )
    assert validate_widget_html(html) == []


def test_widget_contract_accepts_inline_style_attribute():
    assert validate_widget_html('<div style="color:red">hi</div>') == []


def test_widget_contract_scans_brace_free_text_in_linear_time():
    """The naked-CSS rule must not be retried from every offset in the text.

    Written as one pattern, its selector run restarts at each character and
    finds no ``{`` to stop at, which is quadratic in the length of brace-free
    text: 32KB took 4.7s and 128KB took 75s. Widget content is written by the
    agent, so a widget that long is ordinary, not adversarial.
    """
    import time

    html = "<p>" + "a = 1 and b = 2. " * 8_000 + "</p>"
    started = time.perf_counter()
    assert validate_widget_html(html) == []
    assert time.perf_counter() - started < 1.0


def test_widget_contract_accepts_media_query_inside_style_tag():
    html = (
        "<style>@media(prefers-color-scheme:dark){.a{color:#fff}}</style>"
        '<div class="a">x</div>'
    )
    assert validate_widget_html(html) == []


def test_widget_contract_accepts_block_close_tags_with_attributes():
    # HTML allows whitespace and ignored attributes after a closing tag's name
    # (</script\t\n foo> still ends the element); the block stripper must treat
    # such a tag as the end so the script's object literals are not read as
    # naked CSS.
    html = (
        "<script src='x.js' defer>\nvar x = {a: 1, b: 2};\n</script\t\n foo>"
        "<style type='text/css'>.a{color:red}</style >"
        '<div class="a">x</div>'
    )
    assert validate_widget_html(html) == []


def test_widget_contract_rejects_tag_missing_its_opening_bracket():
    # Regression: a widget shipped with the wrapper's "<" gone. The browser read
    # `div style="..."` as a text node, so the card element never existed and
    # every rule it carried — background, border, radius, padding, font — was
    # lost. No other rule sees this: the styling is in an inline style=""
    # attribute, which has no braces for the naked-CSS matcher to catch.
    html = (
        'div style="background:var(--lemma-widget-surface);padding:24px">\n'
        '  <div style="font-size:26px">Good afternoon</div>\n'
        "</div>"
    )
    issues = validate_widget_html(html)
    assert any("missing its opening '<'" in issue for issue in issues)


def test_widget_contract_rejects_stray_end_tag():
    """The mirror of the same mistake: the orphaned close outlives its start."""
    issues = validate_widget_html("<div>counts</div></section>")
    assert any(
        "</section> closes an element that was never opened" in i for i in issues
    )


def test_widget_contract_accepts_implied_end_tags():
    # `</li>`, `</td>` and `</tr>` are optional in HTML, so the open-tag stack is
    # matched by name rather than by exact nesting.
    assert validate_widget_html("<ul><li>open<li>closed</ul>") == []
    assert (
        validate_widget_html("<table><tbody><tr><td>a<td>b</tr></tbody></table>") == []
    )


def test_widget_contract_accepts_void_and_self_closing_elements():
    html = (
        '<div><img src="chart.png"><br><input type="text">'
        '<svg viewBox="0 0 8 8"><path d="M0 0h8"/></svg></div>'
    )
    assert validate_widget_html(html) == []


def test_widget_contract_accepts_escaped_markup_rendered_as_text():
    """A widget showing a code sample spells the bracket `&lt;` deliberately."""
    html = '<pre>&lt;div class="card"&gt;text&lt;/div&gt;</pre>'
    assert validate_widget_html(html) == []


def test_widget_contract_accepts_markup_built_inside_a_script():
    html = (
        '<div id="a"></div>'
        "<script>if (1 < 2) { document.getElementById('a').innerHTML = "
        "'<span class=\"pill\">y</span>'; }</script>"
    )
    assert validate_widget_html(html) == []


def test_widget_contract_rejects_unresolved_starter_tokens():
    issues = validate_widget_html("<div>__WIDGET_TITLE__</div>")
    assert any("__WIDGET_TITLE__" in issue for issue in issues)


def test_widget_contract_requires_config_derived_sdk_loader():
    issues = validate_widget_html(
        "<script>const client = new window.LemmaClient.LemmaClient()</script>"
    )
    assert any("__LEMMA_CONFIG__" in issue for issue in issues)
    assert any("apiUrl" in issue for issue in issues)
    assert any("lemma-client.js" in issue for issue in issues)
    assert any("load handler" in issue for issue in issues)


def test_widget_contract_requires_sdk_onload_boot():
    issues = validate_widget_html(
        """
        <script>
          const cfg = window.__LEMMA_CONFIG__;
          const s = document.createElement('script');
          s.src = cfg.apiUrl + '/public/sdk/lemma-client.js';
          document.head.appendChild(s);
        </script>
        """
    )
    assert any("load handler" in issue for issue in issues)


def test_widget_contract_requires_api_url_identifier():
    issues = validate_widget_html(
        """
        <script>
          const cfg = window.__LEMMA_CONFIG__;
          const s = document.createElement('script');
          s.src = '/public/sdk/lemma-client.js';
          s.onload = boot;
        </script>
        """
    )
    assert any("apiUrl" in issue for issue in issues)
