// What Mail's functions do to the in-memory sample in the "Try it" demo
// (_kit/src/demo.ts). Nothing here leaves the page.
window.__LEMMA_DEMO_HANDLERS__ = {
  // Done, snooze, move, mute, read: what the real function does to the thread.
  mail_thread_action(input, db) {
    const ids = input.conversation_ids || [];
    const a = input.action;
    const change =
      a === "done" ? { state: "done", in_inbox: false }
      : a === "mute" ? { state: "done", muted: true, in_inbox: false }
      : a === "snooze" ? { snoozed_until: String(input.until) }
      : a === "unsnooze" ? { snoozed_until: null }
      : a === "read" ? { unread: false }
      : a === "unread" ? { unread: true }
      : a === "restore" ? { state: "fyi", in_inbox: true, muted: false }
      : a === "move" ? { ...(input.category ? { category: input.category } : {}), ...(input.state ? { state: input.state } : {}), unsure: false, sorted_by: "person" }
      : a === "state" ? { state: input.state, unsure: false, sorted_by: "person" }
      : null;
    if (change) db.patch(ids, change, "mail_thread");
    if (a === "draft") {
      // The Lemma writes the draft; here a short stand-in arrives a moment later.
      db.patch(ids, { draft_state: "writing", draft_hint: input.hint || null, draft_kind: input.kind || "reply" }, "mail_thread");
      setTimeout(() => {
        for (const id of ids) {
          const c = db.find(id) || {};
          const first = String(c.counterpart_name || "").split(/\s+/)[0];
          db.add("draft", {
            conversation_id: id,
            source: "mail",
            source_account: c.source_account || null,
            kind: input.kind || "reply",
            to_handles: c.counterpart_handle ? [c.counterpart_handle] : [],
            cc_handles: [],
            subject: c.subject ? (/^re:/i.test(c.subject) ? c.subject : "Re: " + c.subject) : null,
            body: `Hi${first ? " " + first : ""},\n\nIn the demo, this is where the Lemma writes your ${input.kind === "follow_up" ? "nudge" : "reply"}: in your voice, from the whole thread and the rules you've taught it${input.hint ? `, following “${input.hint}”` : ""}.\n\nCheers`,
            state: "proposed",
            rationale: "A stand-in: in a Lemma the draft is written from the thread, your voice and your rules.",
          });
          db.patch(id, { draft_state: "ready" }, "mail_thread");
        }
      }, 1400);
    }
    return { updated: ids.length, corrections: a === "move" || a === "state" ? ids.length : 0, provider_errors: [] };
  },

  mail_send_draft(input, db) {
    const d = db.find(input.draft_id) || {};
    db.patch(input.draft_id, { state: "sent", sent_at: new Date().toISOString() }, "draft");
    if (d.conversation_id) db.patch(d.conversation_id, { state: "waiting", last_direction: "out", draft_state: "none" }, "conversation");
    return { sent: true, message: "Marked sent. In the demo nothing leaves the page." };
  },

  mail_resort() {
    return { queued: 14 };
  },

  mail_sample_data() {
    return { message: "In the demo the sample stays. In a Lemma this clears it." };
  },
};
