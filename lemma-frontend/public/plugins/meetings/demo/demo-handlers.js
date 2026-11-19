// What Meetings' functions do to the in-memory sample in the "Try it" demo
// (_kit/src/demo.ts). Nothing here leaves the page.
(function () {
  const MEETINGS = "from meeting m join meetings_work";
  const now = () => new Date().toISOString();
  const settingsOf = (db) => db.rows("from meetings_settings")[0] || {};
  const meetingsOf = (db) => db.rows(MEETINGS);
  const time = (v) => {
    const t = Date.parse(v || "");
    return Number.isNaN(t) ? null : t;
  };
  const isOver = (m) => (time(m.end_at) ?? time(m.start_at) ?? Date.now()) <= Date.now();
  const recent = (m) => (time(m.end_at) ?? time(m.start_at) ?? 0) > Date.now() - 7 * 86_400_000;
  const kinds = { customer: "customers", investor: "investors", candidate: "candidates", partner: "partners", internal: "internal meetings", other: "anything else" };
  const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);
  const list = (names) => (names.length < 2 ? names.join("") : names.slice(0, -1).join(", ") + " and " + names[names.length - 1]);

  /** When the brief is due: 18:00 the evening before in the person's zone, or an hour before. */
  function dueFor(start, s) {
    const at = new Date(start);
    if (s.brief_when === "hour_before") return new Date(at.getTime() - 3_600_000).toISOString();
    try {
      const zone = s.timezone || "UTC";
      const parts = (d, withTime) =>
        Object.fromEntries(
          new Intl.DateTimeFormat("en-CA", {
            timeZone: zone, year: "numeric", month: "2-digit", day: "2-digit",
            ...(withTime ? { hour: "2-digit", minute: "2-digit", hourCycle: "h23" } : {}),
          }).formatToParts(d).map((p) => [p.type, p.value]),
        );
      const day = parts(at, false);
      const guess = Date.UTC(+day.year, +day.month - 1, +day.day - 1, 18, 0);
      const wall = parts(new Date(guess), true);
      const shown = Date.UTC(+wall.year, +wall.month - 1, +wall.day, +wall.hour, +wall.minute);
      return new Date(guess - (shown - guess)).toISOString();
    } catch {
      return new Date(at.getTime() - 15 * 3_600_000).toISOString();
    }
  }

  // The writer: a short stand-in arrives a moment later. A meeting the sample
  // already has a brief for gets that brief back.
  const writing = new Set();
  function writeBrief(db, id) {
    db.patch(id, { brief_state: "writing", brief_error: null }, "meetings_work");
    if (writing.has(id)) return;
    writing.add(id);
    setTimeout(() => {
      writing.delete(id);
      const m = db.find(id) || {};
      if (m.brief) {
        db.patch(id, { brief_state: "ready", brief_at: now() }, "meetings_work");
        return;
      }
      const outside = (m.attendees || []).filter((a) => a.outside && !a.you).map((a) => a.name || a.handle);
      const earlier = meetingsOf(db)
        .filter((x) => x.id !== id && isOver(x) && (x.attendees || []).some((a) => a.outside && outside.includes(a.name || a.handle)))
        .map((x) => x.id);
      db.patch(id, {
        brief_state: "ready",
        brief_at: now(),
        headline: "A stand-in for the brief the Lemma writes before this meeting.",
        brief:
          `**In the demo**\n- This is where the Lemma writes the brief, from your mail and earlier meetings with ${outside.length ? list(outside) : "the people on it"}.\n\n` +
          "**In a Lemma**\n- Who they are, and where things stand.\n- What was promised, either way.\n- What to get out of the meeting.",
        searched: [],
        built_from: earlier.length ? { meeting_ids: earlier } : {},
      }, "meetings_work");
    }, 1800);
  }

  /** A meeting ahead that now gets a brief is planned, or written now when it is due. */
  function plan(db, m, wants, s, patch) {
    const start = time(m.start_at);
    if (start == null || start <= Date.now() || m.status === "cancelled") return null;
    if (wants && ["none", "planned", "failed"].includes(m.brief_state || "none")) {
      const due = dueFor(m.start_at, s);
      const state = time(due) <= Date.now() ? "wanted" : "planned";
      Object.assign(patch, { brief_state: state, brief_due_at: due, brief_error: null });
      return state;
    }
    if (!wants && m.brief_state === "planned") Object.assign(patch, { brief_state: "none", brief_due_at: null });
    return patch.brief_state || m.brief_state || "none";
  }

  window.__LEMMA_DEMO_HANDLERS__ = {
    // Settle or change how a meeting was sorted; the rest of its series follows.
    meetings_settle(input, db) {
      const m = db.find(input.meeting_id) || {};
      const s = settingsOf(db);
      const kind = input.kind || m.kind;
      if (!kind) return { kind: null, wants_brief: null, series: 0, brief_state: m.brief_state || "none", message: "Say what kind of meeting it is" };
      const wants = typeof input.wants_brief === "boolean" ? input.wants_brief : (s.kinds_on || []).includes(kind);
      const targets = [m];
      if (m.series_id) {
        for (const x of meetingsOf(db)) {
          if (x.id !== m.id && x.series_id === m.series_id && x.sorted_by !== "person") targets.push(x);
        }
      }
      const when = new Date(m.start_at).toLocaleDateString("en-GB", { day: "2-digit", month: "short" });
      let state = m.brief_state || "none";
      for (const t of targets) {
        const own = t.id === m.id;
        const patch = { kind, wants_brief: wants, sorted_by: "person", unsure: false, reason: own ? "You set this" : `As you set it for the one on ${when}` };
        const next = plan(db, t, wants, s, patch);
        db.patch(t.id, patch, "meeting");
        if (patch.brief_state === "wanted") writeBrief(db, t.id);
        if (own && next) state = patch.brief_state || next;
      }
      const more = targets.length - 1;
      return {
        kind, wants_brief: wants, series: more, brief_state: state,
        message: `Kept as ${kind}, ${wants ? "with a brief" : "no brief"}${more ? `, and ${more} more in the series` : ""}`,
      };
    },

    // The regular pass: briefs that are due, and meetings that are over waiting for notes.
    meetings_due(input, db) {
      const s = settingsOf(db);
      const counts = { briefs_wanted: 0, briefs_planned: 0, notes_wanted: 0, notes_waiting: 0, transcripts: 0, unstuck: 0 };
      const rows = input.meeting_id ? [db.find(input.meeting_id)].filter(Boolean) : meetingsOf(db);
      for (const m of rows) {
        if (m.status === "cancelled") continue;
        if (!isOver(m)) {
          if (input.meeting_id) continue;
          const patch = {};
          plan(db, m, !!m.wants_brief, s, patch);
          if (Object.keys(patch).length && patch.brief_state !== m.brief_state) {
            db.patch(m.id, patch, "meetings_work");
            if (patch.brief_state === "wanted") writeBrief(db, m.id);
          }
          if (patch.brief_state === "wanted") counts.briefs_wanted++;
          if (patch.brief_state === "planned") counts.briefs_planned++;
        } else if (m.wants_brief && recent(m) && s.notes_from !== "skip" && ["none", "waiting", "failed"].includes(m.notes_state || "none")) {
          if (m.notes_state !== "waiting") db.patch(m.id, { notes_state: "waiting", notes_error: null }, "meetings_work");
          counts.notes_waiting++;
        }
      }
      const parts = [];
      if (counts.briefs_wanted) parts.push(`${counts.briefs_wanted} brief${counts.briefs_wanted === 1 ? "" : "s"} being written`);
      if (counts.briefs_planned) parts.push(`${counts.briefs_planned} planned`);
      if (counts.notes_waiting) parts.push(`${counts.notes_waiting} waiting for notes`);
      return { ...counts, errors: [], message: parts.join(", ") || "Nothing due." };
    },

    // Notes the person wrote: kept, then read. The reading is the Lemma's, so a stand-in.
    meetings_add_notes(input, db) {
      const id = input.meeting_id;
      const text = String(input.text || "").trim().replace(/\s+/g, " ");
      db.patch(id, { notes_state: "reading", notes_from: "notes", notes_error: null }, "meetings_work");
      setTimeout(() => {
        const quoted = text.length > 160 ? text.slice(0, 157).trimEnd() + "…" : text;
        db.patch(id, {
          notes_state: "ready",
          summarized_at: now(),
          summary: `Your notes: “${quoted}” In the demo this is where the Lemma reads them, writes up what happened, and lists each decision and promise beside the sentence it came from.`,
        }, "meeting");
      }, 1800);
      return { document_path: "", message: "Notes kept; reading them for decisions and promises." };
    },

    // Which kinds get a brief: meetings the read sorted follow the switch.
    meetings_setup_kinds(input, db) {
      const s = settingsOf(db);
      const order = Object.keys(kinds);
      const on = order.filter((k) => (input.kinds_on || []).includes(k));
      if (s.id) db.patch(s.id, { kinds_on: on }, "meetings_settings");
      for (const m of meetingsOf(db)) {
        if (!["decision", "domains"].includes(m.sorted_by) || m.status === "cancelled") continue;
        const a = m.answers || {};
        let kind = a.kind?.value;
        let brief = a.needs_brief?.value;
        const kc = a.kind?.confidence;
        const bc = a.needs_brief?.confidence;
        if (m.sorted_by === "domains") [kind, brief] = ["internal", true];
        const kindSure = order.includes(kind) && (kc == null || kc >= 0.5 || m.sorted_by === "domains");
        const briefSure = typeof brief === "boolean" && (bc == null || bc >= 0.55 || m.sorted_by === "domains");
        let wants, unsure, reason;
        if (!kindSure) [wants, unsure, reason, kind] = [null, true, m.reason || "Not sure what kind of meeting", null];
        else if (!on.includes(kind)) [wants, unsure, reason] = [false, false, `${cap(kind)}: you don't brief ${kinds[kind]}`];
        else if (!briefSure) [wants, unsure, reason] = [null, true, `${cap(kind)}, not sure it needs a brief`];
        else [wants, unsure, reason] = [brief, false, m.sorted_by === "domains" ? "Only your own people are on it." : `${cap(kind)}, ${brief ? "worth a brief" : "no brief needed"}`];
        if (wants !== m.wants_brief || kind !== m.kind || unsure !== !!m.unsure) {
          db.patch(m.id, { kind, wants_brief: wants, unsure, reason }, "meeting");
        }
      }
      const rows = meetingsOf(db).filter((m) => m.status !== "cancelled");
      const ahead = rows.filter((m) => m.wants_brief === true && !isOver(m)).length;
      const unsure = rows.filter((m) => m.unsure).length;
      return { kinds_on: on, briefs_ahead: ahead, unsure, message: `${ahead} meeting${ahead === 1 ? "" : "s"} ahead will get a brief${unsure ? `, ${unsure} not sure` : ""}` };
    },

    meetings_sample_data() {
      return { message: "In the demo the sample stays. In a Lemma this clears it and starts set-up for your own calendar." };
    },

    // "Write it now", "Write again", "Try again": the brief is asked for on the meeting's own row.
    "PATCH pods/[^/]+/datastore/tables/meetings_work/records/[^/]+"(input, db, ctx) {
      const id = decodeURIComponent(ctx.path.split("/").pop());
      const fields = input.data && typeof input.data === "object" ? input.data : input;
      db.patch(id, fields, "meetings_work");
      if (fields.brief_state === "wanted") writeBrief(db, id);
      const row = db.find(id) || { id, ...fields };
      return { id, data: row, ...row };
    },
  };
})();
