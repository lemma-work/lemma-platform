---
name: browser
description: "Operate a real browser in a Lemma workspace for web pages or local/deployed apps: navigation, screenshots, login flows, forms, scraping, console/network inspection, and saved pages through Agent Browser. Use lemma-app-qa alongside it for systematic app journeys, defect evidence, or release judgment."
---

# Browser

Drive a real Chromium with the `agent-browser` CLI. Use it for anything a page renders: local dev apps, JS-heavy sites, logins, scraping, screenshots, console/network debugging.

## The Core Loop (this is law)

```bash
start-browser <url>              # first use only: starts Xvfb + Chromium + dashboard
agent-browser snapshot -i        # interactive elements with @eN refs
agent-browser click @e3          # act via refs
agent-browser wait --url "**/dashboard"   # semantic wait, never bare sleeps
agent-browser snapshot -i        # ALWAYS re-snapshot after the page changes
```

**Refs go stale the moment the page changes** (navigation, submit, dialog, re-render). Acting on a stale ref is the #1 failure — re-snapshot first. Always use `-i` (interactive-only) to keep output small; add `-u` to include link URLs.

Environment facts:

- Nothing is running at startup — call `start-browser [url]` once, then reuse the same session for everything.
- The session is preconfigured: headed Chromium at `/usr/local/bin/workspace-chrome` on virtual display, persistent profile at `/workspace/.browser-profile` (cookies/logins survive across commands and tasks), session name `workspace`.
- Dashboard on port 4848 for human observation (signed URL via the sandbox runtime Manager `/sandboxes/<id>/browser-url`).
- Local apps: browse `http://127.0.0.1:<port>` from inside the container, never the public preview URL.
- Never install Playwright or browser binaries — everything is preinstalled.

## Acting On Pages

```bash
agent-browser fill @e3 "user@example.com"     # clear + type
agent-browser type @e4 "extra text"           # type without clearing
agent-browser press Enter
agent-browser select @e5 "Option A"
agent-browser check @e6 / uncheck @e6
agent-browser upload @e7 ./file.pdf
agent-browser scroll down 500 ; agent-browser scrollintoview @e9
agent-browser click @e8 --new-tab
```

No snapshot handy? Semantic locators work without one:

```bash
agent-browser find text "Sign In" click
agent-browser find role button click --name "Submit"
agent-browser find label "Email" fill "user@test.com"
```

Raw CSS selectors (`agent-browser click "#submit"`) are the last resort.

## Waiting (pick the right one)

| After | Wait |
| --- | --- |
| Click that navigates | `wait --url "**/new-page"` |
| Form submit | `wait --text "Success"` or `wait --url` |
| SPA update, no URL change | `wait --load networkidle` |
| Element appears dynamically | `wait @e3` or `wait --text "..."` |
| Custom readiness | `wait --fn "window.app.ready === true"` |
| Nothing else fits | `wait 2000` (last resort — slow and flaky) |

## Reading And Extracting

```bash
agent-browser get text @e5 ; agent-browser get attr @e10 href
agent-browser get url ; agent-browser get title
agent-browser --max-output 500000 get html html > page.html   # big output needs --max-output
agent-browser screenshot shot.png ; agent-browser screenshot --full full.png
agent-browser screenshot --annotate map.png                   # numbered labels keyed to @eN refs

# Arbitrary JS — heredoc avoids quote-escaping hell
cat <<'EOF' | agent-browser eval --stdin
Array.from(document.querySelectorAll("table tbody tr")).map(r => ({
  name: r.cells[0].innerText, price: r.cells[1].innerText,
}))
EOF
```

Save pages for later reading/citation (markdown via Readability+Turndown; pdf/jpeg/png direct):

```bash
save-webpage https://example.com/article --formats markdown,pdf --out research
```

## Recipes

**Login + persist.** Fill the form via refs, `wait --url "**/dashboard"`, done — the persistent profile keeps you logged in for later commands. For repeatable logins without secrets in shell history:

```bash
agent-browser auth save my-app --url https://app.example.com/login \
  --username user@example.com --password-stdin     # then: agent-browser auth login my-app
# Or save/reuse cookie state explicitly:
agent-browser state save ./auth.json
agent-browser --state ./auth.json open https://app.example.com
```

**Tabs.** `agent-browser tab` (list), `tab new <url>`, `tab 2`, `tab close 2`. Refs are per-page — re-snapshot after switching.

**Parallel isolated sessions.** `agent-browser --session user-a open ...` — own cookies, tabs, refs per session.

**Dialogs and iframes.** `agent-browser dialog accept|dismiss`; iframes are auto-inlined in snapshots (refs work through them), or `agent-browser frame @e3` / `frame main` to switch context explicitly.

**Local app debugging.** `curl -fsS http://127.0.0.1:<port>` first → `start-browser http://127.0.0.1:<port>` → reproduce → screenshot + console/network logs — don't stop at the visual failure.

## Test a pod app (authenticated)

To exercise a Lemma **app** in the agent browser as the current agent/user, let the
CLI open it *authenticated* instead of wiring tokens by hand:

```bash
lemma apps open support-app                          # a DEPLOYED app, by slug — resolves its served URL + injects the bearer
lemma apps open --url http://localhost:5173 --no-auth # a `npm run dev` app — it already self-authenticates via the dev token
```

`lemma apps open <slug>` registers the current access token as an
`Authorization: Bearer` header scoped to the API origin (so the app's cross-origin
API calls authenticate), then opens the app — no login UI. A local dev server
(`npm run dev`) seeds the token itself, so pass `--url <dev-url> --no-auth`. From
there it's the normal core loop: `snapshot -i` → `click`/`fill` → re-`snapshot`,
plus `screenshot` to capture the rendered UI.

**See it with your own eyes.** Take a `screenshot`, then use the **view-image**
capability on that PNG to actually *view* the rendered app (layout, charts, broken
styles, error overlays). view-image also reads **pod and workspace files
directly** — a downloaded page render (`lemma files child …/pages/page_0001.jpg`),
an uploaded image, or a local screenshot — so you can confirm a chart or document
looks right without a browser. (App design, deploy, and test details:
`lemma-builder/references/apps.md`.)

## Troubleshooting

- Element missing from snapshot → scroll it into view, wait for it, or dismiss the overlay covering it; then re-snapshot.
- Click does nothing → a modal/banner is intercepting; find and dismiss it.
- Fill ignored by custom inputs → `agent-browser keyboard inserttext "text"` bypasses key events.
- CDP/connection errors or weird state → `agent-browser doctor --fix`.
- More guides ship with the CLI: `agent-browser skills list`, `agent-browser skills get <name>`; `references/agent-browser-core.md` has the full core reference.

## See also

- Full core reference (snapshot/ref model, every command) → `references/agent-browser-core.md`
- Build and deploy a pod app → `lemma-builder/references/apps.md`
- Design its product experience → the `lemma-app-design` skill
- Test it systematically and judge a release → the `lemma-app-qa` skill
- Operate the pod (open apps, mint file URLs) → the `lemma-user` skill
- Inline live views over pod data → the `lemma-widget` skill
