---
name: browser
description: "Operate a real browser in a Lemma workspace for web pages or local/deployed apps: navigation, screenshots, login flows, forms, scraping, console/network inspection, and saved pages through Agent Browser. Use lemma-app-qa alongside it for systematic app journeys, defect evidence, or release judgment."
---

# Browser

Drive a real, full desktop Chrome with the `agent-browser` CLI. Use it for anything a page renders: local dev apps, JS-heavy sites, logins, scraping, screenshots, console/network debugging.

**Prefer the typed browser tools** — `browser_open`, `browser_snapshot`, `browser_act`, `browser_read`, `browser_screenshot`, `browser_sign_in` — over raw shell commands. They run the same CLI, and they also yield when a person has taken control of the browser (below). Reach for the shell only for what they do not cover.

## The Core Loop (this is law)

```bash
agent-browser open <url>         # the browser starts itself on first use
agent-browser snapshot -i        # interactive elements with @eN refs
agent-browser click @e3          # act via refs
agent-browser wait --url "**/dashboard"   # semantic wait, never bare sleeps
agent-browser snapshot -i        # ALWAYS re-snapshot after the page changes
```

**Refs go stale the moment the page changes** (navigation, submit, dialog, re-render). Acting on a stale ref is the #1 failure — re-snapshot first. Always use `-i` (interactive-only) to keep output small; add `-u` to include link URLs.

Environment facts:

- Nothing is running at startup, and you do not have to start it. Any `agent-browser` command brings the browser up first if it is down. `start-browser [url]` still exists and is harmless, but it is no longer a step you must remember.
- The browser is **this conversation's own**, not the sandbox's. Its session and profile are set for you; do not pass `--session` or `--profile` yourself unless you genuinely need a second browser (see *Parallel isolated sessions*). Naming one by hand puts you in a different browser from the one a saved login was loaded into — and from the one the panel checks when deciding whether a browser is running for this conversation at all.
- **The profile is scratch, not storage.** Cookies and logins survive across commands *inside a live workspace*, and nothing more: the browser daemon closes Chrome after 2 minutes with no command (`AGENT_BROWSER_IDLE_TIMEOUT_MS=120000`), and suspending the workspace deletes `/tmp/lemma-browser` outright. Never leave the only copy of anything there.
- **Do not save session state into `/workspace`.** `agent-browser state save ./auth.json` writes cookies in plain text onto the durable volume, where it outlives the run that made it and is readable by whatever runs next. Use `browser_sign_in` instead: it asks the person, keeps what they signed in to encrypted and scoped to that one site, and loads it back on the next run without asking again.
- **A person may be watching this browser, and may take it over.** It is streamed live into the workspace app's *Your computer → Browser* panel, where they can press *Take control* and type into the page themselves. While they hold it, the typed browser tools refuse rather than act (exit code 91, with advice); a raw `agent-browser` shell command has no such guard and would type over them. So when you are told somebody is driving, stop and wait — and prefer the typed tools, which know the difference.
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

**Login walls.** You do not sign in. Call `browser_sign_in(origin, reason)` and stop there.

It loads a saved session if there is a working one, and otherwise asks the person, puts the site in front of them, and pauses your run until they answer — however long that takes. When it returns, open the page again and carry on.

```bash
# Never do any of these:
#   ask the person for their password, in the conversation or on a page
#   type a password you were given, or one you found in a file or an env var
#   agent-browser auth save ... --password-stdin
#   agent-browser state save ./auth.json     # plaintext cookies on the durable volume
```

A password is never yours to hold, and a session you save by hand outlives the run that made it. `browser_sign_in` is the only sanctioned path: what it keeps is the site's session, encrypted, scoped to that one site, and visible to the person to remove.

**Tabs.** `agent-browser tab` (list), `tab new <url>`, `tab 2`, `tab close 2`. Refs are per-page — re-snapshot after switching.

**Parallel isolated sessions.** `agent-browser --session user-a --profile /tmp/lemma-browser/profile-user-a open ...` gives a separate browser with its own cookies, tabs and refs. Use it only when you genuinely need two at once — comparing two signed-in users, say.

**Pass `--profile` with `--session`, always.** A session is a whole separate Chrome and needs a profile directory of its own; every session shares one default profile path, and Chrome locks it. `--session` on its own therefore exits immediately with nothing but `Chrome exited early`. Your ordinary commands already run in this conversation's own session with both set for you, which is the other reason not to name one by hand: a person watching sees the whole screen either way, but the panel checks *this conversation's* session to decide whether a browser is running at all.

**Dialogs and iframes.** `agent-browser dialog accept|dismiss`; iframes are auto-inlined in snapshots (refs work through them), or `agent-browser frame @e3` / `frame main` to switch context explicitly.

**Local app debugging.** `curl -fsS http://127.0.0.1:<port>` first → `agent-browser open http://127.0.0.1:<port>` → reproduce → screenshot + console/network logs — don't stop at the visual failure.

## Test a pod app (authenticated)

To exercise a Lemma **app** in the agent browser as the current agent/user, let the
CLI open it *authenticated* instead of wiring tokens by hand:

```bash
lemma apps open support-app                          # a DEPLOYED app, by slug — resolves its served URL + injects the bearer
lemma apps open --url http://localhost:5173 --no-auth # a `npm run dev` app — it already self-authenticates via the dev token
```

`lemma apps open <slug>` seeds the current access token into the app's
`localStorage` (the browser SDK's `injectedToken` mode — the only signal its auth
check honours) *and* registers it as an `Authorization: Bearer` header scoped to
the API origin, so cross-origin API calls authenticate too. Then it opens the app
— no login UI. A local dev server (`npm run dev`) seeds the token itself, so pass
`--url <dev-url> --no-auth`. From there it's the normal core loop: `snapshot -i` →
`click`/`fill` → re-`snapshot`, plus `screenshot` to capture the rendered UI.

**See it with your own eyes.** Take a `screenshot`, then use the **view-image**
capability on that PNG to actually *view* the rendered app (layout, charts, broken
styles, error overlays). view-image reads from **either store** — pass exactly one
of `workspace_file_path` (the sandbox) or `pod_file_path` (the datastore); it
never guesses from the path shape, and giving both or neither is an error. So a
local screenshot, an uploaded image, or a rendered page from a pod document
(`lemma files child …/pages/page_0001.jpg`) all work, and you can confirm a chart
or document looks right without a browser at all. It handles **images only**: a
PDF comes back with a pointer to `pod_view_document_pages`, which renders that
document's pages for you and is the shorter route anyway. (App design, deploy,
and test details: `lemma-builder/references/apps.md`.)

## Troubleshooting

- Element missing from snapshot → scroll it into view, wait for it, or dismiss the overlay covering it; then re-snapshot.
- Click does nothing → a modal/banner is intercepting; find and dismiss it.
- Fill ignored by custom inputs → `agent-browser keyboard inserttext "text"` bypasses key events.
- CDP/connection errors or weird state → `agent-browser doctor` to diagnose. **Do not run `doctor --fix`**: its repairs are destructive — it purges browser state, which throws away a login `browser_sign_in` just restored, and reinstalls Chrome, which replaces the browser this image pins.
- More guides ship with the CLI: `agent-browser skills list`, `agent-browser skills get <name>`; `references/agent-browser-core.md` has the full core reference.

## See also

- Full core reference (snapshot/ref model, every command) → `references/agent-browser-core.md`
- Build and deploy a pod app → `lemma-builder/references/apps.md`
- Design its product experience → the `lemma-app-design` skill
- Test it systematically and judge a release → the `lemma-app-qa` skill
- Operate the pod (open apps, mint file URLs) → the `lemma-user` skill
- Inline live views over pod data → the `lemma-widget` skill
