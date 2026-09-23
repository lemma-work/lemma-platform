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

What is different about this browser:

- **It is the person's, and there is one of it.** Its session and profile are
  set for you: do not pass `--session` or `--profile` unless you genuinely
  need a second browser at the same time (see *Parallel isolated sessions*).
  Naming one by hand opens a different, empty Chrome — not the one that is
  signed in, and not the one the person is watching.
- **Sites stay signed in**, across your run and across conversations, the way
  they do in the browser on their desk. If one is not, `browser_sign_in` asks
  them; you never type a password. Never save session state to a file
  (`agent-browser state save`) — it writes cookies in plaintext that outlive
  your run, and the browser already remembers.
- **Somebody may be watching, and may take over.** If a step lands somewhere
  you did not expect — a page you did not navigate to, a field already
  filled — assume they did it, re-snapshot, and carry on from what is on
  screen. You do not need to wait for them.
- **The window size can change under you**, because it follows whoever is
  watching. If the size matters for a screenshot or a recording, set it
  first: `set-display-size <width> <height>`.
- **Use `$PWD` whenever `agent-browser` writes a file.** It resolves a
  relative path inside the browser daemon, whose working directory is
  whichever conversation happened to start the browser -- not yours. So
  `screenshot ./shot.png` can land in somebody else's directory, and
  `screenshot` with no path goes somewhere you will not find it.

  ```bash
  agent-browser screenshot "$PWD/shot.jpeg"     # lands where you are
  agent-browser pdf "$PWD/page.pdf"
  agent-browser record start "$PWD/run.webm"
  agent-browser screenshot ./shot.jpeg          # DON'T — resolved elsewhere
  ```

  `save-webpage --out` already does this for you, and a shell redirect
  (`agent-browser get html html > page.html`) is written by the shell, so it
  lands where you are as normal.
- **A file the page downloads goes to `~/Downloads`**, not your working
  directory -- that is Chrome's, and one browser serves every conversation.
  Move it if you want it beside your other output.
- **One browser serves every conversation, and it has one active tab.**
  `agent-browser tab new` binds the session to the tab it opens, and every
  command after it acts on that binding -- so a run in another conversation
  that opens a tab takes the binding from you, and your next `get html` or
  `screenshot` is of their page. Nothing fails; you just save the wrong page.
  `save-webpage` takes a lock and is safe. Driving the CLI directly over
  several steps is not, so keep such a sequence short, and re-check `get url`
  before you trust what you are reading.
- Local apps: browse `http://127.0.0.1:<port>`, never the public preview URL.
- Everything is preinstalled. Never install Playwright or a browser.

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
agent-browser screenshot "$PWD/shot.jpeg"        # viewport — what you usually want
agent-browser screenshot --full "$PWD/full.jpeg" # whole scroll: slow, and megabytes
agent-browser screenshot --annotate "$PWD/map.png"            # numbered labels keyed to @eN refs

# Arbitrary JS — heredoc avoids quote-escaping hell
cat <<'EOF' | agent-browser eval --stdin
Array.from(document.querySelectorAll("table tbody tr")).map(r => ({
  name: r.cells[0].innerText, price: r.cells[1].innerText,
}))
EOF
```

**Open, then read. Never `read <url>` in one shot.** Passing a URL to `read`
does not wait for the page to render, and it fails silently — measured in
this sandbox:

| page | `read <url>` | `open <url>` then `read` |
| --- | --- | --- |
| a React app | 1 byte | 1 964 |
| an ad-funded news page | **0 bytes** | 8 522 |
| a static docs page | 4 161 | 5 355 |

Exit code zero every time, so nothing tells you the page was empty. The core
reference shipped with the CLI recommends the one-shot form; it is wrong for
anything that renders client-side, which is most of what needs a browser at
all. `web_fetch` uses the two-step form and checks the result has text in it.

**A screenshot is a file until you look at it.** `screenshot` writes to the sandbox and tells you nothing about what it captured; `view_image` with `workspace_file_path` is what puts the picture in front of you. If this agent's model cannot see images, `view_image` asks one that can and hands you back the description — so set `instructions` to the question you actually have ("is the chart's y-axis labelled?"), not "describe this".

Use it for what a snapshot cannot describe: layout, charts, broken styles, error overlays. Everything textual is cheaper through `snapshot` and `get text`. `--annotate` writes numbered labels keyed to the `@eN` refs, which is how you tell two identical-looking buttons apart. Default to `.jpeg`, and to the viewport. A full-page `.png` is several times the bytes for a photograph of a web page, and a full-page capture of a long article measured 12.4s and 7.0 MB against 2.5s and 76 KB for the viewport. Ask for `--full` only when the part you need is below the fold.

Save pages for later reading/citation (markdown via Readability+Turndown; pdf/jpeg/png direct):

```bash
save-webpage https://example.com/article --formats markdown,pdf --out research
save-webpage https://example.com/article --formats jpeg --full-page   # whole scroll, when you need it
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
- **"Failed to install browser network controls" / "Session with given id not found"** → the daemon's CDP session is stale. `agent-browser close --all`, then open again; one restart is the whole fix. Do not keep re-opening, and do not reach for `doctor --fix`. This one matters more than it reads: installing the network controls is also what answers a proxy's auth challenge, so where a proxy is configured every request comes back **407** and no page loads — a healthy proxy looks like a dead internet.
- CDP/connection errors or weird state → `agent-browser doctor` to diagnose. **Do not run `doctor --fix`**: its repairs are destructive — it purges browser state, which throws away a login `browser_sign_in` just restored, and reinstalls Chrome, which replaces the browser this image pins.
- More guides ship with the CLI: `agent-browser skills list`, `agent-browser skills get <name>`; `references/agent-browser-core.md` has the full core reference.

## See also

- Full core reference (snapshot/ref model, every command) → `references/agent-browser-core.md`
- Build and deploy a pod app → `lemma-builder/references/apps.md`
- Design its product experience → the `lemma-app-design` skill
- Test it systematically and judge a release → the `lemma-app-qa` skill
- Operate the pod (open apps, mint file URLs) → the `lemma-user` skill
- Inline live views over pod data → the `lemma-widget` skill
