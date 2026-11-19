// What People's and Companies' functions do to the in-memory sample in the
// "Try it" demo (_kit/src/demo.ts). Nothing here leaves the page.
(function () {
  const ME = "user-sample";
  const now = () => new Date().toISOString();
  const uuid = () =>
    "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
      const r = (Math.random() * 16) | 0;
      return (c === "x" ? r : (r & 3) | 8).toString(16);
    });
  const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);
  const idFrom = (ctx) => decodeURIComponent(ctx.path.split("/").pop());
  const fieldsOf = (input) => (input.data && typeof input.data === "object" ? input.data : input);
  const firstName = (name, handle) => String(name || handle || "").split(/[\s@]+/)[0];
  const sql = (v) => "'" + String(v).replace(/'/g, "''") + "'";

  const companiesOf = (db) => db.rows("select id, name, domain from company order by name");
  const teamOf = (db) => [...db.rows("from people_link order by"), ...db.rows("from people_link where company_id is not null")];
  const peopleOf = (db) => [...db.rows("where pp.in_scope is true order by pp.last_contact_at"), ...db.rows("pp.company_id is not null limit")];
  const KIND_NAMES = { customer: "a customer", investor: "an investor", partner: "a partner", candidate: "a candidate", vendor: "a vendor", colleague: "a colleague", other: "other" };

  // "Where things stand" is the Lemma's writing: the sample's own when it has
  // one, otherwise a short stand-in a moment later.
  function writeSummary(db, id) {
    db.patch(id, { summary_state: "writing", summary_error: null }, "people_person");
    setTimeout(() => {
      const p = db.find(id) || {};
      if (p.summary) {
        db.patch(id, { summary_state: "ready", summary_at: now() }, "people_person");
        return;
      }
      const h = String(p.handle || "").toLowerCase();
      const convs = h ? db.rows(`lower(h.v) in (${sql(h)}`) : [];
      // A meeting entry is matched by the bare address (see the fixture), so it is looked up by its match.
      const all = db.fixture("pods/pod/datastore/query") || [];
      const meets = (h && all.find((e) => e.match === h)?.response?.items) || [];
      const sources = [
        ...convs.map((c) => ({ kind: "conversation", id: c.id, label: c.subject, at: c.last_message_at })),
        ...meets.map((m) => ({ kind: "meeting", id: m.id, label: m.title, at: m.start_at })),
      ].map((s, i) => ({ n: i + 1, ...s }));
      db.patch(id, {
        summary_state: "ready",
        summary_at: now(),
        summary_sources: sources,
        summary: `In the demo, this is where the Lemma writes where things stand with ${firstName(p.name, h)}: what is going on, what is owed either way and what comes next, each sentence citing the conversation or meeting it came from.`,
      }, "people_person");
    }, 1800);
  }

  // A company look-up is a web search the Lemma writes up: a stand-in here.
  function lookUp(db, id) {
    db.patch(id, { research_state: "writing", research_error: null }, "people_company");
    setTimeout(() => {
      const c = companiesOf(db).find((x) => x.id === id) || {};
      db.patch(id, {
        research_state: "ready",
        researched_at: now(),
        research_sources: [],
        research: `In the demo, this is where the Lemma looks ${c.name || "the company"} up on the web: what they do, how big they are and where, kept here with its sources for the whole team.`,
      }, "people_company");
    }, 1800);
  }

  function moveTo(db, from, to, test) {
    for (const p of peopleOf(db)) {
      if (p.company_id === from && test(p.handle)) db.patch(p.id, { company_id: to.id, company_name: to.name, company_domain: to.domain }, "people_person");
    }
    for (const l of teamOf(db)) if (l.company_id === from && test(l.handle)) db.patch(l.id, { company_id: to.id }, "people_link");
  }

  window.__LEMMA_DEMO_HANDLERS__ = {
    // Who someone is: someone you deal with or not, what they are to you, or someone only the team knows, added.
    people_correct(input, db) {
      let pid = input.person_id;
      const handle = String(input.handle || "").trim().toLowerCase();
      if (!pid && handle) {
        const known = db.rows("from person_handle where kind = 'email'").find((h) => String(h.handle).toLowerCase() === handle);
        const listed = peopleOf(db).find((p) => String(p.handle || "").toLowerCase() === handle);
        pid = (known && known.person_id) || (listed && listed.id) || null;
        if (!pid) {
          const t = teamOf(db).find((l) => String(l.handle).toLowerCase() === handle) || {};
          const c = companiesOf(db).find((x) => x.id === t.company_id) || {};
          const name = String(input.name || t.name || "").trim() || null;
          const row = db.add("people_person", {
            id: uuid(), handle, domain: handle.split("@")[1] || null, in_scope: true, deal_with: true, kind: null,
            sorted_by: "person", sorted_at: now(), unsure: true, reason: "You keep them; what are they to you?", answers: null,
            company_id: t.company_id || null, company_name: c.name || null, company_domain: c.domain || null,
            name, org: null, title: t.title || null, is_vip: false,
            first_contact_at: null, last_contact_at: null, last_contact_dir: null, conversation_count: 0, meeting_count: 0,
            episodes: 0, usual_gap_days: null, warmth: "new", quiet: false, quiet_days: null, waiting_on_me: false,
            next_meeting_at: null, next_meeting_id: null, summary: null, summary_sources: null, summary_state: "none", summary_at: null,
          });
          return { ok: true, person_id: row.id, corrections: 1, message: `Added ${name || handle}.` };
        }
      }
      const p = (pid && db.find(pid)) || null;
      if (!p) return { ok: false, corrections: 0, message: "Say who: a person or an address." };
      const patch = { sorted_by: "person", sorted_at: now(), unsure: false };
      if (typeof input.deal_with === "boolean") {
        patch.deal_with = input.deal_with;
        patch.in_scope = true;
        if (!input.deal_with) Object.assign(patch, { kind: null, quiet: false });
      }
      if (input.deal_with === true && input.kind == null && !p.kind) Object.assign(patch, { unsure: true, reason: "You keep them; what are they to you?" });
      if (input.kind != null) Object.assign(patch, { kind: input.kind, deal_with: true });
      db.patch(pid, patch, "people_person");
      if (input.deal_with === false && p.handle) {
        for (const l of teamOf(db)) if (l.member_id === ME && String(l.handle).toLowerCase() === String(p.handle).toLowerCase()) db.remove(l.id, "people_link");
      }
      const who = p.name || p.handle || "They";
      const message =
        input.deal_with === false ? `Removed ${who}. Reads learn from it.`
        : input.kind != null ? `${who} is ${KIND_NAMES[input.kind] || input.kind}. Reads learn from it.`
        : input.deal_with ? `Kept ${who}. Reads learn from it.`
        : "Nothing changed.";
      return { ok: true, person_id: pid, corrections: 1, message };
    },

    // Rename, merge, or move a domain out: rows only.
    people_company_edit(input, db) {
      const companies = companiesOf(db);
      const c = companies.find((x) => x.id === input.company_id);
      if (!c) return { ok: false, message: "That company isn't here any more." };
      const domains = db.rows("from company_domain order by domain").filter((d) => d.company_id === c.id);
      if (input.action === "rename") {
        const name = String(input.name || "").trim();
        if (!name) return { ok: false, message: "Give it a name." };
        db.patch(c.id, { name, name_from: "person" }, "company");
        for (const p of peopleOf(db)) if (p.company_id === c.id) db.patch(p.id, { company_name: name }, "people_person");
        return { ok: true, company_id: c.id, message: `Renamed to ${name}.` };
      }
      if (input.action === "merge") {
        const into = companies.find((x) => x.id === input.into_id);
        if (!into || into.id === c.id) return { ok: false, message: "Pick another company to merge into." };
        for (const d of domains) db.patch(d.id, { company_id: into.id }, "company_domain");
        moveTo(db, c.id, into, () => true);
        db.remove(c.id, "company");
        const names = domains.map((d) => d.domain);
        return { ok: true, company_id: into.id, message: `Merged ${c.name} into ${into.name}: ${names.join(", ")} now belong${names.length === 1 ? "s" : ""} to it.` };
      }
      if (input.action === "split") {
        const domain = String(input.domain || "").trim().toLowerCase();
        const row = domains.find((d) => d.domain === domain);
        if (!row) return { ok: false, message: `${domain || "That domain"} isn't one of ${c.name}'s.` };
        if (domains.length < 2) return { ok: false, message: "It's the company's only domain." };
        const made = db.add("company", { id: uuid(), name: domain.split(".")[0].split(/[-_]+/).map(cap).join(" "), domain, name_from: "domain" });
        db.patch(row.id, { company_id: made.id }, "company_domain");
        moveTo(db, c.id, made, (h) => String(h || "").toLowerCase().endsWith("@" + domain));
        return { ok: true, company_id: made.id, message: `${domain} is a company of its own now.` };
      }
      return { ok: false, message: `Unknown action '${input.action}'.` };
    },

    people_sample_data(input) {
      return {
        message: input.mode === "clear"
          ? "In the demo the sample stays. In a Lemma, Clear removes exactly these made-up rows."
          : "In the demo nothing more is added. In a Lemma this adds a few made-up people with months of history, and a made-up colleague in the team view.",
      };
    },

    // "Where things stand" asked for (on opening someone, or Write again).
    "PATCH pods/[^/]+/datastore/tables/people_person/records/[^/]+"(input, db, ctx) {
      const id = idFrom(ctx);
      const fields = fieldsOf(input);
      db.patch(id, fields, "people_person");
      if (fields.summary_state === "wanted") writeSummary(db, id);
      const row = db.find(id) || { id, ...fields };
      return { id, data: row, ...row };
    },

    // A company's look-up and the team's notes.
    "POST pods/[^/]+/datastore/tables/people_company/records"(input, db) {
      const fields = fieldsOf(input);
      const row = db.add("people_company", fields);
      if (fields.research_state === "wanted") lookUp(db, row.id);
      return { id: row.id, data: row, ...row };
    },
    "PATCH pods/[^/]+/datastore/tables/people_company/records/[^/]+"(input, db, ctx) {
      const id = idFrom(ctx);
      const fields = fieldsOf(input);
      db.patch(id, fields, "people_company");
      if (fields.research_state === "wanted") lookUp(db, id);
      const row = db.find(id) || { id, ...fields };
      return { id, data: row, ...row };
    },

    // Settings save as usual; "Set up again" would leave the sample for set-up, so in the demo it doesn't.
    "PATCH pods/[^/]+/datastore/tables/people_settings/records/[^/]+"(input, db, ctx) {
      const id = idFrom(ctx);
      const fields = { ...fieldsOf(input) };
      if (fields.setup_step && fields.setup_step !== "done") {
        delete fields.setup_step;
        delete fields.setup_run_id;
        db.note("In the demo set-up doesn't run again. In a Lemma it asks the same three questions, and anything you said about someone stays.");
      }
      db.patch(id, fields, "people_settings");
      const row = db.find(id) || { id, ...fields };
      return { id, data: row, ...row };
    },
  };
})();
