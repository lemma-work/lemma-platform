// What Deal tracker's writes do to the in-memory sample in the "Try it" demo
// (_kit/src/demo.ts). Nothing here leaves the page.
//
// Every change a person makes to a deal goes through deals_save, as in a
// Lemma: the deal changes and each changed field gets a history row.
(function () {
  const DEALS = "from deal order by last_activity_at";
  const STAGES = "from deals_stage";
  const CHANGES = "from deals_change where state = 'proposed'";
  const EDITABLE = ["name", "stage", "amount", "currency", "close_date", "next_step", "lost_reason", "people",
    "owner_user_id", "owner_name", "company_name", "company_domain"];
  const ME = "user-sample";
  const made = new Set(); // deals added in this page
  const madeStages = new Map(); // stages added in this page: id -> row

  const now = () => new Date().toISOString();
  const wrap = (v) => (v == null || typeof v === "object" ? v : { v });
  const clip = (v, n) => {
    const t = v == null ? "" : String(v).trim().replace(/\s+/g, " ");
    return t ? t.slice(0, n) : null;
  };
  const num = (v) => {
    if (v == null || v === "") return null;
    const n = Number(String(v).replace(/[^0-9.\-]/g, ""));
    return Number.isFinite(n) && n >= 0 ? n : null;
  };
  const dayOf = (v) => (/^\d{4}-\d{2}-\d{2}/.test(String(v || "")) ? String(v).slice(0, 10) : null);
  function same(a, b) {
    if (typeof a === "number" || typeof b === "number") return a == null || b == null ? a == b : Math.abs(Number(a) - Number(b)) < 0.005;
    if ((a && typeof a === "object") || (b && typeof b === "object")) return JSON.stringify(a ?? null) === JSON.stringify(b ?? null);
    return (a || null) === (b || null);
  }

  const myName = (db) => (db.rows("from deals_member")[0] || {}).display_name || "Sam Rivera";
  const teamCurrency = (db) => (db.rows("from deals_settings")[0] || {}).currency || "USD";
  function stages(db) {
    const list = [...db.rows(STAGES), ...madeStages.values()].filter((s) => s && db.find(String(s.id)));
    return list.map((s) => db.find(String(s.id)) || s).sort((a, b) => (a.position ?? 0) - (b.position ?? 0));
  }
  const stageNamed = (db, name) => stages(db).find((s) => s.name.toLowerCase() === String(name || "").toLowerCase());
  const firstOpen = (db) => stages(db).find((s) => s.kind === "open");
  function deal(db, id) {
    const row = db.rows(DEALS).find((r) => String(r.id) === String(id));
    return row || (made.has(String(id)) ? db.find(String(id)) : null);
  }
  const allDeals = (db) => [...db.rows(DEALS), ...[...made].map((id) => db.find(id)).filter(Boolean)];

  function history(db, dealId, field, before, after, how, at, source, withName) {
    db.add("deals_history", {
      deal_id: dealId, field, from_value: wrap(before ?? null), to_value: wrap(after ?? null), by_user_id: ME,
      by_name: myName(db), how, source: source || null, with_name: withName || null, at: at || now(),
    });
  }

  // The changed fields, written with one history row each (deals_save's apply).
  function apply(db, d, patch, how, at, source, withName) {
    const clean = {};
    for (const [key, raw] of Object.entries(patch || {})) {
      if (!EDITABLE.includes(key)) continue;
      let value = raw;
      if (key === "stage") {
        const s = stageNamed(db, value);
        if (!s) throw new Error(`There is no stage called ${value}.`);
        value = s.name;
      } else if (key === "amount") value = num(value);
      else if (key === "currency") value = clip(value, 3) ? clip(value, 3).toUpperCase() : null;
      else if (key === "close_date") value = dayOf(value);
      else if (["name", "next_step", "lost_reason", "owner_name", "company_name"].includes(key)) {
        value = clip(value, key === "name" ? 200 : 300);
        if (key === "name" && !value) throw new Error("A deal needs a name: what they're buying.");
      }
      if (!same(d[key], value)) clean[key] = value;
    }
    if ("stage" in clean) {
      const kind = stageNamed(db, clean.stage).kind;
      clean.status = kind;
      clean.closed_at = kind === "won" || kind === "lost" ? at || now() : null;
    }
    if (!Object.keys(clean).length) return [];
    clean.last_activity_at = [String(d.last_activity_at || ""), at || now()].sort().pop();
    db.patch(String(d.id), clean, "deal");
    const changed = [];
    for (const key of Object.keys(clean)) {
      if (["status", "closed_at", "last_activity_at", "company_id", "owner_name"].includes(key)) continue;
      if (key === "owner_user_id") {
        history(db, d.id, "owner", d.owner_name, clean.owner_name || patch.owner_name, how, at, source, withName);
        changed.push("owner");
        continue;
      }
      const field = key === "stage" ? (clean.status === "won" ? "won" : clean.status === "lost" ? "lost" : "stage") : key;
      history(db, d.id, field, d[key], clean[key], how, at, source, withName);
      changed.push(field);
    }
    return changed;
  }

  // A company the sample already knows by its domain.
  function company(db, domain, name) {
    const known = allDeals(db).find((d) => (domain && d.company_domain === domain) || (name && d.company_name && d.company_name.toLowerCase() === name.toLowerCase()));
    return known ? { id: known.company_id, name: known.company_name, domain: known.company_domain } : { id: null, name, domain };
  }

  function addDeal(db, row) {
    const made1 = db.add("deal", row);
    made.add(String(made1.id));
    return made1;
  }

  function save(input, db) {
    const action = String(input.action || "").trim();
    const t = now();

    if (action === "keep" || action === "dismiss") {
      const c = db.rows(CHANGES).find((r) => String(r.id) === String(input.change_id)) || db.find(String(input.change_id));
      if (!c) throw new Error("That proposal isn't yours or is gone.");
      if (c.state !== "proposed") return { deal_id: c.deal_id, message: `Already ${c.state}.` };
      if (action === "dismiss") {
        db.patch(String(c.id), { state: "dismissed", decided_at: t }, "deals_change");
        return { deal_id: c.deal_id, message: "Dismissed. The next read learns from it." };
      }
      const value = { ...(c.value || {}) };
      for (const [k, v] of Object.entries(input.fields || {})) if (k in value || EDITABLE.includes(k)) value[k] = v;
      const withName = ((c.people || [])[0] || {}).name || c.speaker;
      const at = c.said_at || t;
      if (c.kind === "new_deal") {
        const stage = stageNamed(db, value.stage) || firstOpen(db);
        const name = clip(value.name, 200) || c.deal_name || "New deal";
        const found = company(db, c.company_domain, c.company_name);
        const amount = num(value.amount);
        const row = addDeal(db, {
          name, company_id: found.id || c.company_id, company_name: found.name || c.company_name, company_domain: found.domain || c.company_domain,
          people: c.people || [], owner_user_id: ME, owner_name: myName(db), stage: stage.name, status: stage.kind, amount,
          currency: amount != null ? value.currency || teamCurrency(db) : teamCurrency(db), close_date: dayOf(value.close_date),
          next_step: clip(value.next_step, 300), closed_at: stage.kind !== "open" ? at : null, lost_reason: null,
          source: c.source || "mail", source_id: null, last_activity_at: at,
        });
        history(db, row.id, "created", null, { name, stage: stage.name, amount }, "proposal", at, c.source, withName);
        db.patch(String(c.id), { state: "kept", decided_at: t, deal_id: row.id, kept_value: value }, "deals_change");
        return { deal_id: row.id, changed: ["created"], message: `Added ${name}.` };
      }
      const d = deal(db, c.deal_id);
      if (!d) {
        db.patch(String(c.id), { state: "expired", decided_at: t }, "deals_change");
        throw new Error("That deal is gone.");
      }
      const patch = Object.fromEntries(Object.entries(value).filter(([k]) => EDITABLE.includes(k)));
      if (c.kind === "lost" && !("stage" in patch)) {
        const lost = stages(db).find((s) => s.kind === "lost");
        if (lost) patch.stage = lost.name;
      }
      const changed = apply(db, d, patch, "proposal", at, c.source, withName);
      db.patch(String(c.id), { state: "kept", decided_at: t, kept_value: value }, "deals_change");
      return { deal_id: d.id, changed, message: changed.length ? "Kept." : "Kept; the deal already said so." };
    }

    if (action === "create") {
      const f = input.fields || {};
      const name = clip(f.name, 200);
      if (!name) throw new Error("A deal needs a name: what they're buying.");
      const stage = stageNamed(db, f.stage) || firstOpen(db);
      const people = (f.people || []).filter((p) => p && p.handle);
      let domain = clip(f.company_domain, 200);
      if (!domain && people[0] && people[0].handle.includes("@")) domain = people[0].handle.split("@")[1].toLowerCase();
      const found = company(db, domain, clip(f.company_name, 200));
      const amount = num(f.amount);
      const row = addDeal(db, {
        name, company_id: found.id, company_name: found.name || clip(f.company_name, 200), company_domain: found.domain || domain, people,
        owner_user_id: ME, owner_name: myName(db), stage: stage.name, status: stage.kind, amount,
        currency: String(f.currency || teamCurrency(db)).toUpperCase().slice(0, 3), close_date: dayOf(f.close_date),
        next_step: clip(f.next_step, 300), closed_at: stage.kind !== "open" ? t : null, lost_reason: null, source: "person", source_id: null,
        last_activity_at: t,
      });
      history(db, row.id, "created", null, { name, stage: stage.name, amount }, "person");
      return { deal_id: row.id, changed: ["created"], message: `Added ${name}.` };
    }

    const d = input.deal_id ? deal(db, input.deal_id) : null;
    if (!d) throw new Error("That deal is gone.");

    if (action === "edit") {
      const fields = { ...(input.fields || {}) };
      if ("owner_user_id" in fields && !fields.owner_name) fields.owner_name = fields.owner_user_id === ME ? myName(db) : null;
      const changed = apply(db, d, fields, "person");
      return { deal_id: d.id, changed, message: changed.length ? "Saved." : "Nothing changed." };
    }
    if (action === "note") {
      const body = String(input.note || "").trim().slice(0, 4000);
      if (!body) throw new Error("The note is empty.");
      db.add("deals_note", { deal_id: d.id, body, by_user_id: ME, by_name: myName(db) });
      db.patch(String(d.id), { last_activity_at: t }, "deal");
      return { deal_id: d.id, changed: ["note"], message: "Note added." };
    }
    if (action === "delete") {
      for (const c of db.rows(CHANGES)) if (String(c.deal_id) === String(d.id) && c.state === "proposed") db.patch(String(c.id), { state: "expired", decided_at: t }, "deals_change");
      db.remove(String(d.id), "deal");
      return { deal_id: null, changed: ["deleted"], message: `Deleted ${d.name}.` };
    }
    throw new Error(`Unknown action ${action}.`);
  }

  window.__LEMMA_DEMO_HANDLERS__ = {
    deals_save: save,

    // Settings: stages and the team's currency. Renaming a stage renames it on
    // every deal; a removed stage sends its deals to the first open stage.
    deals_setup_apply(input, db) {
      const existing = stages(db);
      let renamed = 0;
      let moved = 0;
      if (Array.isArray(input.stages)) {
        const order = { open: 0, won: 1, lost: 2 };
        const wanted = input.stages
          .map((s) => ({ id: s && s.id, name: clip(s && s.name, 80), kind: ["open", "won", "lost"].includes(s && s.kind) ? s.kind : "open", meaning: clip(s && s.meaning, 300) }))
          .filter((s) => s.name);
        const names = wanted.map((w) => w.name.toLowerCase());
        if (new Set(names).size !== names.length) throw new Error("Two stages have the same name.");
        if (!["open", "won", "lost"].every((k) => wanted.some((w) => w.kind === k))) throw new Error("Keep at least one open stage, one for won and one for lost.");
        wanted.sort((a, b) => order[a.kind] - order[b.kind]);
        const kept = new Set();
        wanted.forEach((w, position) => {
          const old = existing.find((s) => s.id === w.id) || existing.find((s) => s.name.toLowerCase() === w.name.toLowerCase());
          const row = { name: w.name, kind: w.kind, meaning: w.meaning, position };
          if (old && !kept.has(old.id)) {
            kept.add(old.id);
            db.patch(String(old.id), row, "deals_stage");
            if (madeStages.has(String(old.id))) madeStages.set(String(old.id), { ...madeStages.get(String(old.id)), ...row });
            if (old.name !== w.name) {
              for (const d of allDeals(db)) if (d.stage === old.name) { db.patch(String(d.id), { stage: w.name, status: w.kind }, "deal"); renamed += 1; }
            }
          } else {
            const s = db.add("deals_stage", row);
            madeStages.set(String(s.id), s);
          }
        });
        const open = wanted.find((w) => w.kind === "open").name;
        for (const s of existing.filter((x) => !kept.has(x.id))) {
          for (const d of allDeals(db)) {
            if (d.stage !== s.name) continue;
            db.patch(String(d.id), { stage: open, status: "open", closed_at: null }, "deal");
            history(db, d.id, "stage", s.name, open, "person");
            moved += 1;
          }
          db.remove(String(s.id), "deals_stage");
          madeStages.delete(String(s.id));
        }
      }
      const settings = db.rows("from deals_settings")[0];
      const currency = String(input.currency || (settings && settings.currency) || "USD").trim().toUpperCase();
      if (!/^[A-Z]{3}$/.test(currency)) throw new Error("A currency is three letters, like EUR or USD.");
      if (settings) db.patch(String(settings.id), { currency }, "deals_settings");
      const parts = [`${stages(db).length} stages`, currency];
      if (renamed) parts.push(`${renamed} deals renamed`);
      if (moved) parts.push(`${moved} deals moved`);
      return { stages: stages(db), currency, renamed, moved, message: parts.join(", ") };
    },

    deals_sample_data(input) {
      return {
        message: input.mode === "clear"
          ? "In the demo the sample stays: it's all there is here."
          : "In the demo the sample is already here. In a Lemma this adds four made-up deals with weeks of history.",
      };
    },

    // What needs the person, for Today: nothing to show in this demo.
    deals_needs_you() {
      return { written: 0 };
    },

    // "Write it now" on the weekly review runs its schedule. The summary is the
    // Lemma writing, so the sample's own comes back, a moment later.
    "POST pods/[^/]+/schedules/[^/]+/runs"(input, db) {
      const review = db.rows("from deals_review order by week_end")[0];
      if (review) {
        const { summary } = review;
        db.patch(String(review.id), { summary_state: "writing", summary: null }, "deals_review");
        setTimeout(() => db.patch(String(review.id), { summary_state: "ready", summary, summary_at: now() }, "deals_review"), 2500);
      }
      db.note("In the demo the summary is the sample's own. In a Lemma this is where the Lemma writes it fresh from this week's counts.");
      return { id: "demo-run", status: "RUNNING" };
    },
  };
})();
