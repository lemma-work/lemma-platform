---
name: browser
description: "Operate a real browser in a Lemma workspace for web pages or local/deployed apps: navigation, screenshots, login flows, forms, scraping, console/network inspection, and saved pages through Agent Browser. Use lemma-app-qa alongside it for systematic app journeys, defect evidence, or release judgment."
---

# Browser

Drive a real, full desktop Chrome with the `agent-browser` CLI. Use it for anything a page renders: local dev apps, JS-heavy sites, logins, scraping, screenshots, console/network debugging.

The CLI is the whole surface. There is one browser tool, `browser_sign_in`, and it exists because a login is the one thing the command line must not do (below). Everything else is `agent-browser` through `exec_command`, and `view_image` to look at what it captures.

## The Core Loop (this is law)

```bash
agent-browser open <url>         # the browser starts itself on first use
agent-browser snapshot -i        # interactive elements with @eN refs
agent-browser click @e3          # act via refs
agent-browser wait --url "**/dashboard"   # semantic wait, never bare sleeps
agent-browser snapshot -i        # ALWAYS re-snapshot after the page changes
```

**Refs go stale the moment the page changes** (navigation, submit, dialog, re-render). Acting on a stale ref is the #1 failure — re-snapshot first. Always use `-i` (interactive-only) to keep output small; add `-u` to include link URLs.

**Chain with `&&` and end on a snapshot.** Each `exec_command` is a round trip, and the steps between two snapshots are usually not decisions — you already know you will click, wait and re-read. One command is one round trip:

```bash
agent-browser click @e3 && agent-browser wait --url "**/dashboard" && agent-browser snapshot -i
```

Split them only where you genuinely need to see the output before choosing the next step. `&&` also stops at the first failure, so a click that missed does not go on to report a snapshot of the page it failed to leave.

Environment facts:

- Nothing is running at startup, and you do not have to start it. Any `agent-browser` command brings the browser up first if it is down. `start-browser [url]` still exists and is harmless, but it is no longer a step you must remember.
- There is **one browser per sandbox**, and it is the person's. Its session and profile are set for you; do not pass `--session` or `--profile` yourself unless you genuinely need a second browser at the same time (see *Parallel isolated sessions*). Naming one by hand opens a different, empty Chrome — not the one that is signed in and not the one the panel shows.
- **The profile is durable, and it is the person's.** It lives in their home (`~/.lemma/browser/profile`), so a site they have signed in to stays signed in — across your run, across a suspend, and across conversations, exactly as the browser on their own desk does. Chrome itself still comes and goes: the daemon closes it after five idle minutes and the memory guard may kill it under pressure. That costs you a cold start, not the login.
- **Do not write session state to disk yourself.** `agent-browser state save ./auth.json` puts cookies in plain text on the durable root, where they outlive the run that made them and are readable by whatever runs next. There is also no reason to: the profile already persists. If a site is not signed in, `browser_sign_in` asks the person.
- **A person may be watching this browser, and may take it over.** It is streamed live into the workspace app's *Your computer → Browser* panel, where they can click and type in the page themselves. Nothing stops you acting at the same time and you do not need to wait: this is your browser and they are looking over your shoulder. If a step lands somewhere you did not expect — a page you did not navigate to, a field already filled — assume they did it, re-snapshot, and carry on from what is on screen rather than from what you last saw.
- **The window size is shared, and it moves.** One display serves the sandbox. While somebody has the *Your computer → Browser* panel open it is sized to match their pane; when the last of them closes it, it returns to 1440x960. So the viewport you measured at the start of a run may not be the one you have now, and the usual casualty is a recording or a set of screenshots that turn out to be the wrong shape afterwards. If the size matters, set it yourself and say so: `set-display-size <width> <height>` (bounded by `WORKSPACE_XVFB_MAX_SCREEN`), then take the recording. Re-read it with `xrandr --current` if you need to be sure rather than hopeful.
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
agent-browser screenshot shot.jpeg ; agent-browser screenshot --full full.jpeg
agent-browser screenshot --annotate map.png                   # numbered labels keyed to @eN refs

# Arbitrary JS — heredoc avoids quote-escaping hell
cat <<'EOF' | agent-browser eval --stdin
Array.from(document.querySelectorAll("table tbody tr")).map(r => ({
  name: r.cells[0].innerText, price: r.cells[1].innerText,
}))
EOF
```

**A screenshot is a file until you look at it.** `screenshot` writes to the sandbox and tells you nothing about what it captured; `view_image` with `workspace_file_path` is what puts the picture in front of you. If this agent's model cannot see images, `view_image` asks one that can and hands you back the description — so set `instructions` to the question you actually have ("is the chart's y-axis labelled?"), not "describe this".

Use it for what a snapshot cannot describe: layout, charts, broken styles, error overlays. Everything textual is cheaper through `snapshot` and `get text`. `--annotate` writes numbered labels keyed to the `@eN` refs, which is how you tell two identical-looking buttons apart. Default to `.jpeg` — a full-page `.png` is several times the bytes for a photograph of a web page.

Save pages for later reading/citation (markdown via Readability+Turndown; pdf/jpeg/png direct):

```bash
save-webpage https://example.com/article --formats markdown,pdf --out research
```

## Recipes

**Login walls.** You do not sign in. Call `browser_sign_in(origin, reason)` and stop there.

It opens the site and looks. If the browser is already signed in — which it often is, because the profile is durable and somebody may have signed in weeks ago in another conversation — it returns immediately and you carry on. Otherwise it puts the site in front of the person and pauses your run until they answer, however long that takes.

```bash
# Never do any of these:
#   ask the person for their password, in the conversation or on a page
#   type a password you were given, or one you found in a file or an env var
#   agent-browser auth save ... --password-stdin
#   agent-browser state save ./auth.json     # plaintext cookies on the durable disk
```

A password is never yours to hold. `browser_sign_in` is the only sanctioned path, and what it produces is the person's own browser session, held by their browser — visible to them on the connectors page, where signing out really signs the browser out.

**Tabs.** `agent-browser tab` (list), `tab new <url>`, `tab 2`, `tab close 2`. Refs are per-page — re-snapshot after switching.

**Parallel isolated sessions.** `agent-browser --session user-a --profile /tmp/lemma-browser/profile-user-a open ...` gives a separate browser with its own cookies, tabs and refs. Use it only when you genuinely need two at once — comparing two signed-in users, say.

**Pass `--profile` with `--session`, always.** A session is a whole separate Chrome and needs a profile directory of its own; Chrome locks the one it opens. `--session` on its own therefore exits immediately with nothing but `Chrome exited early`. A named session's profile is also **scratch** — under `/tmp`, gone with the sandbox — so anything you sign in to there is lost. That is deliberate: the durable profile is the default one, and a second browser is for comparing two accounts, not for keeping one.

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

Then `screenshot` + `view_image` as above, to see the rendered app rather than
infer it. `view_image` reads from **either store** — pass exactly one of
`workspace_file_path` (the sandbox) or `pod_file_path` (the datastore); it never
guesses from the path shape, and giving both or neither is an error. So an
uploaded image or a rendered page from a pod document (`lemma files child
…/pages/page_0001.jpg`) works too, and you can confirm a chart or document looks
right without a browser at all. It handles **images only**: a PDF comes back with
a pointer to `pod_view_document_pages`, which renders that document's pages for
you and is the shorter route anyway. (App design, deploy, and test details:
`lemma-builder/references/apps.md`.)

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
