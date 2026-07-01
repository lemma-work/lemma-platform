# Share → Import → Remix: the one story

> Working doc (draft). The growth loop built on top of the pod export/import engine.
> Owner: deepak · Status: spec + in-progress

## The loop in one breath

Deepak builds Trumpet → hits **Share** → gets a link + a GitHub repo with an
"Import to Lemma" badge. Lekhika clicks the badge → lands on
`lemma.work/import/github/deepak/trumpet` → signs up *in the act of importing* →
picks **create a new pod** → consents → watches it build → lands on a takeover
screen that says *Trumpet is yours* → opens the app, drops it in Slack, tweaks
the agent → hits **Share**. The loop closes; now there are two billboards.

`make → share → import → remix → share`

## Three invariants (already built — reuse, don't rebuild)

1. **One bundle.** `pod.json` + resource dirs is the only artifact. A link, a
   GitHub repo, and a `.zip` are three envelopes around the same bundle.
2. **One engine.** The `pod_imports` state machine + the wizard
   (consent → requirements → resolve → apply → health) runs *every* import,
   whatever the entry point. All entries converge on the same wizard once the
   bundle is resolved.
3. **One URL namespace.** `lemma.work/import/...` is the spine:
   - `/import/p/<id>` — a shared pod
   - `/import/github/<owner>/<repo>` — a repo
   - `/import/g/<slug>` — gallery (later)
   Logged-out → sign up, then continue. Acquisition is *inside* the loop.

## The beats

1. **Share** — primary `Share` button on every pod. Produces (in viral-leverage
   order): a **link** (`/import/p/<id>`), **Publish to GitHub**, **Download .zip**.
   Export is the stateless `GET`; stash the zip as a pod file, hand back its URL.
2. **Publish to GitHub** — user's own repo first; gallery-PR later. Creates
   `github.com/<user>/<pod>` with bundle + generated README + **import badge**
   (`[![Import to Lemma](badge.svg)](https://lemma.work/import/github/<user>/<pod>)`).
   The badge is our "Deploy to Vercel" button — durable, free distribution.
3. **Land** — any entry resolves the bundle to a named card + capability chips
   *before* consent, then the one new decision: **create a new pod** (default,
   full ownership) vs **install into this pod** (additive graft).
4. **Build** — the existing wizard: consent, requirements, resolve, apply
   (deferred grant pass), health. Failures are resumable + self-explaining.
5. **Live — full-takeover remix screen.** A dedicated celebratory page (not a
   wizard footer). *Trumpet is yours.* Four actions, ordered to pivot consumer →
   creator: **view app → activate a surface → share with a friend → customize**.
   "Share" here, at peak delight, re-enters beat 1.

## Provenance (the thread that makes it a network)

On import, write `source` onto pod config:
`{kind: "github" | "link" | "upload", ref: "deepak/trumpet", import_id}`.
Unlocks: attribution on the remix ("remixed from Trumpet by @deepak"), a
"N pods remixed from this" counter on the original (social proof feeding beat 1),
and later "source changed — pull updates".

## Build arc (each layer leaves a complete, wider loop)

- **L1 — spine + basic loop:** `Share` button, `/import/*` routes, link envelope,
  create-new vs install-here, provenance field. Shareable end-to-end via link.
- **L2 — remix takeover:** the live screen, four actions, share wired to beat 1.
- **L3 — GitHub:** OAuth, repo creation, README + badge, `/import/github/...`.
- **L4 — gallery:** `lemma.work/gallery`, registry, gallery-PR as 2nd publish target.

## Decisions locked
- GitHub publish: **both, user-repo first**.
- Remix screen: **full takeover**.

## L1 + L2 task checklist
- [x] Backend: `source` on pod config (`PodConfig.source` / `PodSource`).
- [x] Backend: "create new pod from bundle" — `POST /imports` (names from `pod.json`,
      stamps provenance, dedups name). e2e tested.
- [x] Backend: shared-link resolve — `POST /imports/from-pod/{id}` (export an
      existing pod → new pod for the caller, provenance `kind="link"`). e2e tested.
- [x] Frontend: `Share` sheet (copy link + download, GitHub placeholder) on the pod.
- [x] Frontend: create-new vs install-here fork in the wizard; apply keyed off `imp.pod_id`.
- [x] Frontend: `/import/p/<id>` route → resolves link into the wizard (review → apply).
- [x] Frontend (L2): remix takeover — four actions (view app, surface, share, customize)
      on a create-new completion.
- [ ] L3: GitHub publish (OAuth, repo + README + badge) and `/import/github/<owner>/<repo>`.
- [ ] Follow-up: public/token sharing (today the link is org-scoped) and the lemma.work
      sign-up-in-the-loop redirect.
