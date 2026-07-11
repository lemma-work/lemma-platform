"""HTML checks for the shared app/widget browser-SDK contract.

``lint_app_html`` remains advisory for app uploads: callers log common authoring
mistakes without rejecting a bundle. ``validate_widget_html`` promotes those same
mistakes plus fragment/starter/loader checks to blocking errors before an inline
widget is persisted and rendered.

The browser SDK is served only from the API origin, so widgets and apps must build
its URL from the injected ``window.__LEMMA_CONFIG__.apiUrl`` and boot their code
from the dynamically created script's load handler.
"""

from __future__ import annotations

import re


_LINT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"@babel/standalone|type\s*=\s*['\"]text/babel['\"]", re.IGNORECASE),
        "Runtime Babel (`@babel/standalone` / `text/babel`) is not supported for apps — precompile JSX or use plain JS.",
    ),
    (
        re.compile(
            r"@lemma/pod-client|\bLemmaPodClient\b|\bcreateIframeTokenProvider\b"
        ),
        "Uses the retired `@lemma/pod-client` SDK. Load `/public/sdk/lemma-client.js` and use "
        "`new window.LemmaClient.LemmaClient()` instead.",
    ),
    (
        re.compile(
            r"<script[^>]+src=['\"][^'\"]*/(?:public/sdk|sdk)/pod-client\.js['\"]",
            re.IGNORECASE,
        ),
        'Loads the retired `pod-client.js`. Use `<script src="/public/sdk/lemma-client.js"></script>`.',
    ),
    (
        re.compile(
            r"\bsrc\s*=\s*['\"]https?://[^'\"]*/public/sdk/lemma-client\.js['\"]",
            re.IGNORECASE,
        ),
        "Hardcodes an absolute host for the SDK script. Build the URL from "
        "`window.__LEMMA_CONFIG__.apiUrl` (the API origin) and load it in a dynamically "
        "created `<script>` that boots in `onload` — never the app's own subdomain.",
    ),
    (
        re.compile(
            r"\bsrc\s*=\s*['\"]/(?:public/sdk|sdk)/lemma-(?:client|ui)\.js['\"]",
            re.IGNORECASE,
        ),
        "Loads an SDK bundle (`lemma-client.js` / `lemma-ui.js`) with a relative "
        "`/public/sdk/...` src, which 404s on app subdomains (only the API origin serves "
        "the SDK). Build the URL from `window.__LEMMA_CONFIG__.apiUrl` and boot in the "
        "script's `onload` — see the `lemma-widget` skill's \"Loading the SDK\".",
    ),
    (
        re.compile(r"new\s+window\.LemmaClient\s*\("),
        "`new window.LemmaClient(...)` references the namespace object, not the constructor — "
        "use `new window.LemmaClient.LemmaClient()`.",
    ),
)

_HARDCODED_POD_ID = re.compile(
    r"podId\s*:\s*['\"][0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}['\"]"
)
_FULL_DOCUMENT = re.compile(
    r"<!doctype|<html[\s>]|<head[\s>]|<body[\s>]", re.IGNORECASE
)
_UNRESOLVED_TEMPLATE_TOKEN = re.compile(r"__[A-Z][A-Z0-9_]*__")
_RUNTIME_CONFIG_REFERENCE = re.compile(r"\b(?:window\.)?__LEMMA_CONFIG__\b")
_API_URL_IDENTIFIER = re.compile(r"\bapiUrl\b")


def lint_app_html(html: str) -> list[str]:
    """Return advisory warnings for app/widget HTML. Never raises; ``[]`` == clean."""
    warnings: list[str] = []
    for pattern, message in _LINT_PATTERNS:
        if pattern.search(html):
            warnings.append(message)
    if _HARDCODED_POD_ID.search(html):
        warnings.append(
            "Hardcoded pod id passed to the SDK. Construct "
            "`new window.LemmaClient.LemmaClient()` with no args; the host injects "
            "`window.__LEMMA_CONFIG__`."
        )
    return warnings


def validate_widget_html(html: str) -> list[str]:
    """Return blocking authoring errors for an inline widget fragment."""
    content = (html or "").strip()
    if not content:
        return ["Widget content must not be empty."]

    errors = list(lint_app_html(content))
    if _FULL_DOCUMENT.search(content):
        errors.append(
            "Widget content must be an HTML/SVG fragment without doctype, html, head, or body tags."
        )

    tokens = sorted(
        set(_UNRESOLVED_TEMPLATE_TOKEN.findall(content)) - {"__LEMMA_CONFIG__"}
    )
    if tokens:
        errors.append(
            "Replace unresolved widget starter tokens before display: "
            + ", ".join(tokens)
            + "."
        )

    uses_sdk = "lemma-client.js" in content or "LemmaClient" in content
    if uses_sdk:
        if not _RUNTIME_CONFIG_REFERENCE.search(content):
            errors.append(
                "SDK-backed widgets must read window.__LEMMA_CONFIG__ at runtime."
            )
        if not _API_URL_IDENTIFIER.search(content):
            errors.append(
                "Build the browser SDK URL from window.__LEMMA_CONFIG__.apiUrl."
            )
        if "lemma-client.js" not in content:
            errors.append(
                "The widget uses LemmaClient but does not load /public/sdk/lemma-client.js."
            )
        if not re.search(
            r"\.onload\s*=|addEventListener\(\s*['\"]load['\"]",
            content,
            re.IGNORECASE,
        ):
            errors.append(
                "Boot SDK-backed widget code from the SDK script's load handler."
            )

    return list(dict.fromkeys(errors))
