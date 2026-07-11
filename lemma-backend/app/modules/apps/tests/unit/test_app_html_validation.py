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
    assert validate_widget_html("<svg><circle cx='5' cy='5' r='5'/></svg>") == []


def test_widget_contract_accepts_portable_sdk_fragment():
    assert validate_widget_html(UNIFIED_OK) == []


def test_widget_contract_accepts_direct_runtime_config_api_url():
    html = UNIFIED_OK.replace(
        "cfg.apiUrl", "window.__LEMMA_CONFIG__.apiUrl"
    )
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
