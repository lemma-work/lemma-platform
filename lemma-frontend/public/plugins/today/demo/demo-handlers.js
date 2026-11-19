// What Today's actions do to the in-memory sample in the "Try it" demo
// (_kit/src/demo.ts). Nothing here leaves the page.
(function () {
  const firstName = (name, handle) => String(name || handle || "").split(/[\s@]+/)[0];

  // The plugin that wrote an item drafts its nudge; here a short stand-in arrives a moment later.
  function draftNudge(db, id) {
    setTimeout(() => {
      const it = db.find(id);
      if (!it || it.request !== "nudge") return;
      const first = firstName(it.person_name, it.person_handle);
      db.patch(id, {
        request: null,
        prepared: "Nudge drafted in your voice",
        prepared_text:
          `Hi${first ? " " + first : ""},\n\n` +
          `In the demo, this is where the Lemma writes a short, kind nudge about “${it.title}”, in your voice, from the whole conversation.\n\nThanks`,
      }, "needs_you");
    }, 1600);
  }

  window.__LEMMA_DEMO_HANDLERS__ = {
    // Done, Not now, Draft a nudge: Today writes only the person's own state on an item.
    "PATCH pods/[^/]+/datastore/tables/needs_you/records/[^/]+"(input, db, ctx) {
      const id = decodeURIComponent(ctx.path.split("/").pop());
      const fields = input.data && typeof input.data === "object" ? input.data : input;
      const row = { ...(db.find(id) || { id }), ...fields };
      if (fields.state && fields.state !== "open") {
        // The list reads `state = 'open' or (...)`, a WHERE with an OR the demo
        // can't hold a changed row to, so a closed item is taken out here.
        db.patch(id, fields);
        db.remove(id, "needs_you");
      } else {
        // As Mail does while it writes: the item says so, in place of any "No nudge yet".
        const writing = fields.request === "nudge" ? { prepared: "Nudge being written", prepared_text: null } : {};
        db.patch(id, { ...fields, ...writing }, "needs_you");
        if (fields.request === "nudge") draftNudge(db, id);
      }
      return { id, data: row, ...row };
    },

    // "Check again": one run of the person's morning schedule.
    "POST pods/[^/]+/schedules/[^/]+/runs"(_input, db) {
      db.note("In the demo the day isn't checked again. In a Lemma, each morning Today asks every plugin what needs you and lays out the day.");
      return { id: "demo-run", status: "QUEUED" };
    },
  };
})();
