// What Lead research's writes do to the in-memory sample in the "Try it" demo
// (_kit/src/demo.ts). Nothing here leaves the page.
//
// Asking for a look-up puts the person in line, as in a Lemma. The look-up
// itself is the Lemma reading the web and writing what it found, so in the
// demo it doesn't run: the page says where that would happen.
(function () {
  const LEADS = "from leads_person lp join person p on p.id = lp.id left join people_person";
  const CANDIDATES = "from people_person pp join person p on p.id = pp.id left join company c on c.id = pp.company_id left join leads_person lp";
  const made = new Set(); // people put in line from "Look someone up"
  const scored = new Set(); // companies someone scored in this page
  let fitNoted = 0;

  const now = () => new Date().toISOString();
  const firstName = (name, handle) => String(name || handle || "them").split(/\s+/)[0];
  const lookupNote = (name) =>
    `In the demo the look-up doesn't run. In a Lemma this is where the Lemma reads the web and writes what it finds about ${name}: their role, their company, and where each fact came from.`;

  function lead(db, id) {
    return db.rows(LEADS).find((r) => String(r.id) === id) || (made.has(id) ? db.find(id) : null);
  }
  const allLeads = (db) => [...db.rows(LEADS), ...[...made].map((id) => db.find(id)).filter(Boolean)];

  window.__LEMMA_DEMO_HANDLERS__ = {
    leads_request(input, db) {
      const pid = String(input.person_id || "");
      const l = lead(db, pid);
      const cand = db.rows(CANDIDATES).find((c) => String(c.id) === pid);
      if (!l && !cand) return { ok: false, message: "No one like that among your people in People map." };
      const name = (l || cand).name || (l || cand).handle || "them";
      const state = l ? l.state : null;
      if (state === "wanted" || state === "looking") return { ok: true, person_id: pid, state, message: `${name} is being looked up now.` };
      if (state === "ready" && !input.force) {
        return { ok: true, person_id: pid, state, message: `${name} was looked up on ${String(l.looked_up_at || "").slice(0, 10)}. Ask again to look again.` };
      }
      if (state === "ready") {
        // Look up again: what was found before comes back a moment later.
        db.patch(pid, { state: "wanted", wanted_at: now(), forced: true }, "leads_person");
        setTimeout(() => db.patch(pid, { state: "ready", looked_up_at: now() }, "leads_person"), 3000);
        db.note(`In the demo the look-up gives back what it found before. In a Lemma it reads the web again for ${firstName(name)}, in about a minute.`);
        return { ok: true, person_id: pid, state: "wanted", message: `Looking ${name} up again.` };
      }
      const patch = { worth: true, worth_by: "person", worth_at: now(), state: "queued" };
      if (!l || l.worth_by !== "decision") patch.worth_reason = "You asked for it";
      if (l) {
        db.patch(pid, patch, "leads_person");
      } else {
        // The company's fit, as the list's read joins it.
        const known = allLeads(db).find((x) => x.company_id && x.company_id === cand.company_id && x.fit != null);
        db.add("leads_person", {
          ...patch, id: pid, company_id: cand.company_id || null, kind: cand.kind || null, worth_confidence: null, wanted_at: null, forced: false,
          looked_up_at: null, lookup_seconds: null, lookup_error: null, found: null, facts: null, not_found: null, role: null, location: null,
          profile_url: null, summary: null, fit: known ? known.fit : null, fit_sure: known ? known.fit_sure : null, fit_why: known ? known.fit_why : null,
          review: "new", reviewed_at: null, sample: false, name: cand.name, mail_title: null, handle: cand.handle, last_inbound_at: null,
          last_contact_at: cand.last_contact_at || null, next_meeting_at: null, company_name: cand.company_name || null,
          company_domain: cand.company_domain || null,
        });
        made.add(pid);
      }
      db.note(lookupNote(firstName(name)));
      return { ok: true, person_id: pid, state: "queued", message: `${name} is in line for a look-up.` };
    },

    // Worth looking up or not, the company's fit for the whole team, kept or passed.
    leads_correct(input, db) {
      const pid = String(input.person_id || "");
      const l = lead(db, pid);
      if (!l) return { ok: false, message: "No such lead." };
      const name = l.name || "them";
      const said = [];
      let state = l.state;
      if (input.worth != null) {
        const patch = { worth: !!input.worth, worth_by: "person", worth_at: now(), worth_reason: input.worth ? "You said to look them up" : "You said to skip them" };
        if (input.worth && ["skipped", "unsure", "failed"].includes(state)) state = patch.state = "queued";
        else if (!input.worth && ["unsure", "proposed", "queued", "wanted"].includes(state)) state = patch.state = "skipped";
        db.patch(pid, patch, "leads_person");
        said.push(`${name}: ${input.worth ? "look up" : "skip"}`);
        if (input.worth && state === "queued") db.note(lookupNote(firstName(name)));
      }
      if (input.fit != null) {
        const score = Math.max(1, Math.min(5, Math.round(Number(input.fit))));
        const cid = l.company_id;
        if (!cid) return { ok: false, state, message: "They have no company to score." };
        const by = (db.rows("from leads_settings")[0] || {}).display_name || "Sam";
        const why = `${by} said ${score} of 5`;
        const row = { fit: score, fit_sure: true, fit_confidence: null, fit_why: why, fit_by: "person", fit_by_name: by, fit_at: now() };
        // A row of its own, so the lead's page can say who set the score.
        if (!scored.has(cid)) {
          db.add("leads_company", { id: cid, ...row });
          scored.add(cid);
        }
        db.patch(cid, row, "leads_company");
        for (const x of allLeads(db)) if (x.company_id === cid) db.patch(String(x.id), { fit: score, fit_sure: true, fit_why: why }, "leads_person");
        said.push(`fit ${score} of 5, for the team`);
      }
      if (["new", "kept", "passed"].includes(input.review)) {
        db.patch(pid, { review: input.review, reviewed_at: now() }, "leads_person");
        said.push({ kept: "kept", passed: "passed", new: "back to new" }[input.review]);
      }
      if (!said.length) return { ok: false, state, message: "Nothing to change." };
      return { ok: true, state, message: "Noted: " + said.join(", ") + "." };
    },

    // Scoring a company again is the Lemma reading its facts: one note for the whole pass.
    leads_fit(input, db) {
      if (Date.now() - fitNoted > 10000) {
        fitNoted = Date.now();
        db.note("In the demo the scores stay as they are. In a Lemma this is where the Lemma scores each company again against your description.");
      }
      return { fit: null };
    },

    leads_consider() {
      return { message: "Nobody new to read" };
    },

    leads_sample_data(input) {
      return {
        message: input.mode === "clear"
          ? "In the demo the sample stays: it's all there is here."
          : "In the demo the sample is already here. In a Lemma this adds made-up leads for People map's sample people.",
      };
    },

    // What needs the person, for Today: nothing to show in this demo.
    leads_needs_you() {
      return { written: 0 };
    },

    // "Set up again" would leave the demo for a set-up it can't run; the rest of a settings change goes through.
    "PATCH pods/[^/]+/datastore/tables/leads_settings/records/[^/]+"(input, db, ctx) {
      const id = decodeURIComponent(ctx.path.split("/").pop());
      const fields = { ...(input && input.data && typeof input.data === "object" ? input.data : input || {}) };
      if (fields.setup_step && fields.setup_step !== "done") {
        delete fields.setup_step;
        delete fields.setup_run_id;
        db.note("In the demo set-up doesn't run again. In a Lemma it asks who fits your team, who to look up, and how many a day.");
      }
      if (Object.keys(fields).length) db.patch(id, fields, "leads_settings");
      const row = db.rows("from leads_settings")[0] || { id, ...fields };
      return { id, data: row, ...row };
    },
  };
})();
