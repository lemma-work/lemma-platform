# App cover

Every published app has one large picture: `/.lemma/cover.png` on its own
origin, 1200×630. It is what a link to the app unfurls with in Slack, iMessage,
WhatsApp and X, and what the app's card shows in Lemma's Apps grid.

- If the app's build ships `.lemma/cover.png`, that file is served.
- If it does not, the host draws one from the app's name, description and slug
  — the **letter** cover below. Every app has a cover from its first deploy;
  making your own replaces it.

Make one for every app you build, as the last step before deploying, and ship
it with the app. It needs no one's sign-off.

## The rule: a cover never shows real data

A cover leaves Lemma. Anyone the link reaches sees it, and the services that
unfurl links keep their own copy — deleting the app does not take it back. And
no one person's view is safe to show: the builder usually sees the most rows, so
their screen would show members and outsiders rows they may not open.

So a cover is the app's first screen **with sample rows you write**, rendered
where real data cannot be reached:

- **Never** screenshot the deployed app, or the app under `npm run dev` — the
  dev server signs the browser in and loads real rows.
- **Never** use your default browser session. It is the person's and signed in.
  Use the separate session below.
- **Never** copy rows out of the pod into the sample — not names, not amounts,
  not "just the shape". Invent them.

`scripts/cover_serve.py` is what makes this safe rather than careful. It serves
the built app on localhost and tells the page its API is that same localhost
(`/_lemma`) with a placeholder token, then answers every API call from your
sample files. It holds no credentials and passes nothing on to the real API —
the one exception is the SDK's public script, fetched with no headers.

## Pick a template

The templates live in `/skills/lemma-app-design/assets/cover/`, each with a
finished sample in `examples/` — look at it with `view_image` first.

| Template | Use it when | Screenshot it needs |
|---|---|---|
| `cover-window.html` · `examples/window.png` | **The default.** The app's first screen in a window on the app's colour. | 1280×800 |
| `cover-phone.html` · `examples/phone.png` | The app is used on phones: check-ins, field work, anything people install to a home screen. | 390×844 |
| `cover-letter.html` · `examples/letter.png` | Never render this one. It is what the host draws when the build ships no cover — here so you know what "no cover" looks like. | — |

Pick by the app, not by taste: a desk tool gets the window, a phone tool gets
the phone. The templates are fixed — do not restyle them per app. Lemma's covers
are one family; the app's own screen is what makes each one its own.

## Make it

### 1. Write the sample rows

A folder of JSON files, one per API call the first screen makes. A file's path
is the request's path under `/_lemma/`, plus `.json`, and the pod is always
`pod`:

| The app calls | Write |
|---|---|
| `records.list("deals")` / `useRecords` | `pods/pod/datastore/tables/deals/records.json` |
| `tables.list()` / `useTableList` | `pods/pod/datastore/tables.json` |
| `files.list()` / `useFiles` | `pods/pod/datastore/files.json` |
| `functions.list()` | `pods/pod/functions.json` |
| `datastore.query(sql)` | `pods/pod/datastore/query.json` |

Lists use the API's envelope, and a record is a flat row:

```json
{"items": [{"id": "1", "company": "Northwind", "stage": "Proposal", "amount": 48000, "owner": "Priya"}],
 "limit": 50, "next_page_token": null, "total": 1}
```

For more than one SQL query, make `query.json` a list of
`{"match": "<text from the SQL>", "response": {"items": [...], "total": n}}`;
the first match answers.

Write enough to fill the first screen: five to eight rows, a mix of states, and
one that shows the app's point — the deal in negotiation, the person who is
stuck. Use made-up companies and first names, never `test` or `foo`. Sign-in
needs no file: the server answers as a sample person, Sam Rivera.

### 2. Serve the production build with the sample rows

```bash
npm run build                                   # a Vite app; an HTML app has nothing to build
python3 /skills/lemma-app-design/scripts/cover_serve.py \
  --app dist --fixtures /tmp/cover/fixtures --name "Deals" --port 4610 &
```

`--app` is `dist/` for a Vite app, or the app's own folder for an HTML app.

### 3. Render the first screen

```bash
B="agent-browser --session cover --profile /tmp/lemma-browser/profile-cover"
$B set viewport 1280 800 2                      # phone template: 390 844 2
$B open http://localhost:4610
$B wait --load networkidle
$B screenshot "/tmp/cover/screen.png"
```

Look at `screen.png` with `view_image` before going on. It must show the first
screen filled with your rows. The server prints `MISS GET /_lemma/...` for every
call it had no file for and answered empty: an empty panel, a spinner or a "0"
where there should be work means a file to write. Add it, reload, shoot again.

### 4. Compose the cover

```bash
TEMPLATE=cover-window.html                      # the phone template: cover-phone.html
cp "/skills/lemma-app-design/assets/cover/$TEMPLATE" /tmp/cover/
(cd /tmp/cover && python3 -m http.server 4611 --bind 127.0.0.1) &
URL=$(python3 -c 'import sys, urllib.parse as u; print("http://localhost:4611/" + sys.argv[1] + "?" + u.urlencode(dict(name=sys.argv[2], description=sys.argv[3], address=sys.argv[4], slug=sys.argv[5])))' \
  "$TEMPLATE" "Deals" "Every open deal, who owns it, and what happens next." "deals.lemma.work" "deals")
$B set viewport 1200 630 1
$B open "$URL"
$B wait --fn "document.body.dataset.ready"
$B eval "document.body.dataset.ready"           # must be "yes"
$B screenshot "$PWD/public/.lemma/cover.png"    # an HTML app: "$PWD/.lemma/cover.png"
```

- The template reads the screenshot from `screen.png` beside it, which is
  where step 3 saved it.
- `name` and `description` are the app's own (`lemma apps get <name>`), so the
  picture says what the link preview's text says.
- `address` is where the app is served, without `https://`.
- `slug` is the app's `public_slug`; it picks the colour the app's icon uses.

View the result with `view_image`, then close the `cover` session and stop both
servers.

### 5. Ship it with the app

The cover travels in the build at `.lemma/cover.png`:

- **Vite:** `public/.lemma/cover.png` — Vite copies it into `dist/`.
- **HTML app:** `.lemma/cover.png` beside `index.html`. A single-file app has
  nowhere to put it: make it a folder (`index.html` + `.lemma/`) first.

Deploy as usual, then check the one being served is yours:

```bash
curl -s -o /tmp/cover/served.png https://<slug>.<app-domain>/.lemma/cover.png
```

View `served.png`. If it is the letter cover, the build did not carry
`.lemma/cover.png` — look in `dist/.lemma/`.

## When the app changes

The cover is part of the build, so a redeploy keeps the old one until you
replace it. Make a new one when the first screen changes enough that the cover
would mislead: a new layout, a renamed app, a different first view. A cosmetic
fix does not need one.
