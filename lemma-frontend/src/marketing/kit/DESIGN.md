# Kit launch studio

For a launch owner, turn draft launch assets into reviewed versions by putting the rendered asset beside its copy, feedback, and release requirements.

This is an explicitly fictional public demo, not a connected pod. Existing React/Next preview routing and sample identity remain authoritative. No external publishing, messaging, or backend writes. Drafts, reviews, comments and release date live in versioned sessionStorage for this browser tab. If storage fails, editing continues in memory with a visible notice.

## Surface contract

- Asset rail: four seeded assets; selection controls the canvas and review panel.
- Landing preview: actual HTML page composed from editable headline/body/button text. Toggle mobile/desktop and compare saved versions.
- Announcement: email rendering with editable subject and body.
- Storyboard: three selectable frames, editable voiceover and individual replacement state.
- Customer story: rendered story; identified version blocked until explicit sample permission is recorded or anonymized.
- Review: comments tied to asset and revision; request changes requires a note; approving requires no pending edits and resolved asset blockers. Editing invalidates approval; saving creates an immutable version.
- Release view: readiness derived from current asset reviews, editable date, unresolved blockers, review history. No publish button.
- Export: download current draft as plain text, naming the actual revision.

## Direction

Quiet production studio chrome, warm paper canvases, ink #202723, forest #234c3c, cream #f6f4ed, muted #647168, amber #875a25. System sans for tools, serif for the campaign landing headline. Default screen shows a finished-looking landing-page composition at review scale, surrounded by modest controls. No generic status banners, no invented handoff prose.

## Journeys and states

Default: landing asset v3, pending review. Edit copy -> unsaved preview -> save v4 -> compare v3/v4 -> add version-linked comment -> approve v4 -> release view updates. Later edit clears approval. Storyboard updates are explicit sample checkboxes, never imply a video was rendered. Customer story starts blocked and can be anonymized. Empty comments invite a note; invalid actions explain the condition; storage errors preserve in-memory work.

At <=1000px the review panel flows below the canvas. At <=600px assets become a horizontal rail, controls wrap, and the canvas fits without horizontal body overflow. Buttons have focus outlines; inputs have labels; validation and status changes use live regions. Verify desktop and 375px rendered pixels and the whole edit/save/compare/review loop.
