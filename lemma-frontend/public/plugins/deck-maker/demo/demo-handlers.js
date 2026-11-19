// What Deck maker's work does to the in-memory sample in the "Try it" demo
// (_kit/src/demo.ts). Nothing here leaves the page.
//
// In a Lemma a request is a decks_job row, and the decks-work run writes the
// cards. Here the job rows are answered in the page: approving an outline
// writes its cards one by one (from words written ahead of time), an ask is
// matched to the cards it names, a change of look or a removal is made (they
// need no writer), and a rewrite says what the writer would do.
(function () {
  const now = () => new Date().toISOString();
  const obj = (v) => (v && typeof v === "object" && !Array.isArray(v) ? v : typeof v === "string" ? safe(v, {}) : {});
  const list = (v) => (Array.isArray(v) ? v : typeof v === "string" ? safe(v, []) : []);
  function safe(text, fallback) {
    try {
      return JSON.parse(text);
    } catch {
      return fallback;
    }
  }
  const later = (ms) => new Promise((r) => setTimeout(r, ms));

  // The two sample outlines, written ahead of time: what the writer would make of each line.
  const WRITTEN = {
    // Support backlog: the plan for November
    "11f3dc45-6704-4d4e-8abe-4a079f834867": { layout: "hero", blocks: [{ type: "text", text: "Monday team meeting · where the queue stands and what changes this month" }], notes: "Say up front: three changes, and we leave with an owner for each." },
    "fc0d3f59-5015-4ac0-988e-0385d42b5829": { layout: "bullets", blocks: [{ type: "bullets", items: [
      { icon: "layers", text: "**212** tickets open, up from 168 at the start of October" },
      { icon: "clock", text: "Median wait for a second reply: **3 days**" },
      { icon: "users", text: "The longest waits sit with our five biggest accounts" },
    ] }], notes: "Read the numbers off the tracker on the screen, so everyone sees the same queue." },
    "f54dc408-fed4-4271-a182-ce788f165c9f": { layout: "two-column", blocks: [{ type: "columns", columns: [
      { icon: "wrench", heading: "Waiting on us", body: "128 tickets: answers to write, fixes to test, refunds to send." },
      { icon: "repeat", heading: "Waiting elsewhere", body: "84 tickets parked on a customer reply or another team, with no one chasing them." },
    ] }], notes: "This month's changes go after the right-hand pile." },
    "46b8a37b-38e1-4bf3-97fc-9300f4f120a5": { layout: "prose", blocks: [{ type: "text", text: "Tickets rarely stall on the first answer. They stall **after** it: nobody owns the follow-up, an escalation lands in a shared queue, and a ticket waiting on another team waits until someone notices." }], notes: "Ask who picked up the last escalation that came back. Usually nobody can say." },
    "ef88b4a3-ad1b-4184-b0f5-a1cc38396519": { layout: "bullets", blocks: [{ type: "bullets", items: [
      { icon: "target", text: "One named owner per ticket, from first reply to close" },
      { icon: "calendar", text: "A daily 15-minute pass over the oldest tickets" },
      { icon: "send", text: "Escalations come back to a person, not a queue" },
    ] }], notes: "Each change is small on its own. Together they close the gaps from the last card." },
    "029f33f4-a509-49f7-ad34-94287982b75b": { layout: "closing", blocks: [{ type: "heading", text: "Agree the owners before we leave" }, { type: "text", text: "Three changes, a name against each, and a date to look at the queue together again." }], notes: "Don't end the meeting until each change has a name." },
    // Two new cities by spring
    "dc790475-fee3-537f-bc2c-8813a25fa47c": { layout: "hero", blocks: [{ type: "text", text: "Fernwick Coffee Roasters · the plan for next quarter" }], notes: "One decision at the end: which city first." },
    "d28e4126-d33f-5622-9b0d-987bd252f459": { layout: "prose", blocks: [{ type: "text", text: "Offices filled up again this autumn. Enquiries from outside our city **tripled** since September, and nine of them came from two places we don't deliver to yet." }], notes: "The demand is already there. The question is only where we start." },
    "650c4c57-6588-5d1c-86e0-8ac7d6aee4af": { layout: "two-column", blocks: [{ type: "columns", columns: [
      { icon: "map-pin", heading: "Leeds", body: "Five enquiries, a unit we can lease from January, and a two-hour drive for the first deliveries." },
      { icon: "map-pin", heading: "Bristol", body: "Four enquiries and bigger offices, but no unit until March and a longer hire." },
    ] }], notes: "Leeds is faster. Bristol is bigger. Both work; we can only open one first." },
    "b4a88d73-4a6c-55cb-89f7-40b90f6c2a25": { layout: "stats", blocks: [{ type: "stats", items: [
      { icon: "dollar-sign", value: "£86k", label: "to fit out a second roastery" },
      { icon: "users", value: "£9k", label: "a month in new wages" },
      { icon: "trending-up", value: "14 months", label: "to pay back, at 40 offices" },
    ] }, { type: "text", text: "Figures from this year's quotes; payback assumes our current average order." }], notes: "The fit-out is the big number. Wages are the one that keeps coming." },
    "26341992-4089-55de-a587-51f0127a3332": { layout: "bullets", blocks: [{ type: "bullets", items: [
      { icon: "coffee", text: "Two roasters, trained here for a month before we open" },
      { icon: "package", text: "One driver with a van, from the first week" },
      { icon: "calendar", text: "Offers out by mid-January to open in March" },
    ] }], notes: "Hiring sets the date more than the building does." },
    "13b3d1ac-b5b5-55d3-b06d-540b513a51dd": { layout: "closing", blocks: [{ type: "heading", text: "Decide the first city this month" }, { type: "text", text: "Pick Leeds or Bristol, and we sign the lease and start hiring next week." }], notes: "Ask for the decision in the room." },
  };

  /** What the writer would make of a card the sample didn't write ahead: its own outline line. */
  function fallback(card) {
    const text = String(card.outline || card.title || "").trim() || "A card from your outline.";
    switch (card.layout) {
      case "closing":
        return [{ type: "heading", text: card.title || "Next steps" }, { type: "text", text }];
      case "bullets":
        return [{ type: "bullets", items: text.split(/;\s*|\.\s+(?=[A-Z])/).map((t) => t.trim().replace(/\.$/, "")).filter(Boolean).slice(0, 4).map((t) => ({ text: t })) }];
      case "quote":
        return [{ type: "quote", text, attribution: null }];
      case "visual-left":
      case "visual-right":
        return [{ type: "text", text }, { type: "visual", hint: card.title || "" }];
      default:
        return [{ type: "text", text }];
    }
  }

  /* ---------- the sample's rows, as they are now ---------- */

  // Rows this page added to decks_card (an outline card, a card put back), so a deck's cards include them.
  const extraCards = new Set();

  function deckCards(db, deckId) {
    const rows = db.rows("select * from decks_card").filter((c) => c.deck_id === deckId);
    const have = new Set(rows.map((c) => c.id));
    for (const id of extraCards) {
      const row = db.find(id);
      if (row && row.deck_id === deckId && !have.has(id)) rows.push(row);
    }
    return rows.sort((a, b) => Number(a.position) - Number(b.position));
  }

  function renumber(db, cards) {
    cards.forEach((c, i) => {
      if (Number(c.position) !== i + 1) db.patch(c.id, { position: i + 1 }, "decks_card");
    });
  }

  function themeLook(t) {
    return { ...obj(t.tokens), source: "theme", theme_id: t.id, name: t.name };
  }

  const tag = (cards) => cards.map((c) => `card ${c.position}`).join(", ");
  const several = (cards) => (cards.length === 1 ? `card ${cards[0].position}` : `cards ${cards.map((c) => c.position).join(", ")}`);

  /* ---------- which cards an ask is about ---------- */

  const STOP = new Set(["make", "more", "less", "card", "cards", "the", "this", "that", "with", "into", "about", "shorter", "longer", "punchier", "sharper",
    "better", "clearer", "please", "slide", "slides", "deck", "turn", "change", "rewrite", "some", "them", "there", "their", "what", "from", "have", "should", "would", "could", "it's"]);

  function decide(request, cards, themes, deck) {
    const t = request.toLowerCase();
    const named = themes.find((th) => new RegExp(`\\b${th.name.toLowerCase()}\\b`).test(t));
    if (named || /\b(look|theme|colou?rs?|fonts?)\b/.test(t)) {
      const current = obj(deck.look).theme_id;
      const pick = named
        || (/\bdark\b/.test(t) ? themes.find((th) => obj(th.tokens).dark && th.id !== current) : null)
        || (/\blight\b/.test(t) ? themes.find((th) => !obj(th.tokens).dark && th.id !== current) : null);
      return pick ? { action: "look", cards: [], theme: pick, confidence: 0.9 } : { action: "look", cards: [], confidence: 0.4 };
    }
    const action = /\b(remove|delete|drop|cut)\b/.test(t) ? "remove" : /\b(add|insert)\b/.test(t) ? "add" : "rewrite";
    const byPos = (n) => cards.find((c) => Number(c.position) === n);
    const numbers = [];
    for (const m of t.matchAll(/\bcards?\s+(\d+(?:\s*(?:,|and|&|or)\s*\d+)*)/g)) for (const n of m[1].match(/\d+/g)) numbers.push(Number(n));
    let picked = numbers.map(byPos).filter(Boolean);
    let confidence = 0.97;
    if (!picked.length && action === "add" && /\bbefore the (last|closing|final)\b/.test(t) && cards.length > 1) picked = [cards[cards.length - 2]];
    if (!picked.length && /\b(closing|last|final|ending)\b/.test(t)) picked = [cards[cards.length - 1]];
    if (!picked.length && /\b(title card|first card|opening|cover)\b/.test(t)) picked = [cards[0]];
    if (!picked.length && /\b(titles|whole deck|every card|all the cards|each card|formal|tone|voice)\b/.test(t)) {
      picked = cards.slice();
      confidence = 0.8;
    }
    if (!picked.length) {
      const words = (t.match(/[a-z][a-z'-]{3,}/g) || []).filter((w) => !STOP.has(w));
      let best = null;
      let bestScore = 0;
      for (const c of cards) {
        const hay = `${c.title || ""} ${c.outline || ""} ${JSON.stringify(c.blocks || "")}`.toLowerCase();
        const score = words.reduce((n, w) => n + (hay.includes(w.replace(/s$/, "")) ? 1 : 0), 0);
        if (score > bestScore) {
          best = c;
          bestScore = score;
        }
      }
      if (best) {
        picked = [best];
        confidence = Math.min(0.92, 0.62 + 0.12 * bestScore);
      }
    }
    if (!picked.length && action === "add" && cards.length) picked = [cards[cards.length - (cards[cards.length - 1].layout === "closing" ? 2 : 1)] || cards[0]];
    if (!picked.length) return { action, cards: [], confidence: 0.4 };
    return { action, cards: picked, confidence };
  }

  /* ---------- the work a job row asks for ---------- */

  async function writeCards(db, job) {
    const deck = db.find(String(job.deck_id));
    if (!deck) return;
    db.patch(job.id, { state: "working", started_at: now() }, "decks_job");
    const todo = deckCards(db, deck.id).filter((c) => c.status !== "ready" || !list(c.blocks).length);
    const all = deckCards(db, deck.id).length;
    for (const card of todo) {
      await later(500);
      db.patch(card.id, { status: "writing" }, "decks_card");
      await later(650);
      const canned = WRITTEN[card.id];
      const fresh = db.find(card.id) || card;
      const blocks = canned && canned.layout === fresh.layout ? canned.blocks : fallback(fresh);
      db.patch(card.id, { status: "ready", blocks, notes: canned && canned.layout === fresh.layout ? canned.notes : null, version: 1, updated_at: now() }, "decks_card");
    }
    await later(300);
    db.patch(deck.id, { status: "ready", ready_at: now(), updated_at: now() }, "decks_deck");
    db.patch(job.id, { state: "done", finished_at: now(), summary: `Wrote ${all} of ${all} cards.` }, "decks_job");
    db.note("In the demo these cards were written ahead of time. In a Lemma, the writer writes each one from your outline, in the deck's look.");
  }

  async function rewriteOutline(db, job) {
    db.patch(job.id, { state: "working", started_at: now() }, "decks_job");
    await later(1800);
    const deck = db.find(String(job.deck_id));
    if (deck) db.patch(deck.id, { status: "outline_ready", error: null, updated_at: now() }, "decks_deck");
    db.patch(job.id, { state: "done", finished_at: now(), summary: "Outline ready." }, "decks_job");
    db.note("In the demo the outline isn't written again. In a Lemma, the outliner writes a new one from the same sentence.");
  }

  async function edit(db, job) {
    const deck = db.find(String(job.deck_id));
    if (!deck) return;
    const cards = deckCards(db, deck.id).filter((c) => c.status === "ready");
    const themes = db.rows("select * from decks_theme");
    db.patch(job.id, { state: "working", started_at: now() }, "decks_job");
    await later(1200);

    const chosen = list(job.chosen).map((id) => cards.find((c) => c.id === id)).filter(Boolean);
    const d = chosen.length
      ? { action: ["rewrite", "add", "remove"].includes(job.action) ? job.action : "rewrite", cards: chosen, confidence: null }
      : decide(String(job.request || ""), cards, themes, deck);
    const words = { rewrite: "Rewrite", add: "Add a card after", remove: "Remove", look: "Change the look" };
    const reason = `${words[d.action] || "Not sure"}: ${d.cards.length ? tag(d.cards) : "no card"}${d.confidence != null ? ` (${Math.round(d.confidence * 100)}% sure)` : ""}`;
    const decided = { action: d.action, cards: chosen.length ? list(job.cards) : d.cards.map((c) => c.id), confidence: d.confidence, reason };

    if (d.action === "look") {
      if (!d.theme) {
        db.patch(job.id, { ...decided, state: "ask", reason: "Which look did you mean?" }, "decks_job");
        db.note("Name a look to change to: " + themes.map((th) => th.name).join(", ") + ".");
        return;
      }
      db.patch(deck.id, { look: themeLook(d.theme), updated_at: now() }, "decks_deck");
      db.patch(job.id, { ...decided, state: "done", finished_at: now(), theme: d.theme.id, before: { look: obj(deck.look), voice: deck.voice || null },
        summary: `Changed the look to ${d.theme.name}. No card's words changed.` }, "decks_job");
      return;
    }
    if (!d.cards.length || (d.confidence != null && d.confidence < 0.5)) {
      db.patch(job.id, { ...decided, state: "ask" }, "decks_job");
      return;
    }
    const positions = Object.fromEntries(deckCards(db, deck.id).map((c) => [c.id, Number(c.position)]));

    if (d.action === "remove") {
      const keep = deckCards(db, deck.id).filter((c) => !d.cards.some((x) => x.id === c.id));
      if (!keep.length) {
        db.patch(job.id, { ...decided, state: "ask", reason: "That would remove every card. Pick the ones to remove." }, "decks_job");
        return;
      }
      const targets = d.cards.map((c) => db.find(c.id) || c);
      // Moved out of the deck rather than deleted, so Undo can put them back.
      for (const c of targets) db.patch(c.id, { deck_id: `${deck.id}:removed` }, "decks_card");
      renumber(db, keep);
      db.patch(job.id, { ...decided, state: "done", finished_at: now(), before: { cards: targets, positions },
        summary: `Removed ${tag(d.cards)}.` }, "decks_job");
      return;
    }

    // A rewrite or a new card is the writer's work: the demo shows which cards, and stops there.
    db.patch(job.id, decided, "decks_job");
    await later(1800);
    const what = d.action === "add"
      ? `In the demo no card is added. In a Lemma, the writer adds one after card ${d.cards[d.cards.length - 1].position} from your ask, and nothing else changes.`
      : `In the demo no card is rewritten. In a Lemma, the writer rewrites ${several(d.cards)}, and nothing else changes.`;
    db.patch(job.id, { state: "done", finished_at: now(), before: { cards: [], positions }, summary: what }, "decks_job");
  }

  function work(db, job) {
    if (!job || job.state !== "wanted") return;
    const run = job.kind === "cards" ? writeCards : job.kind === "outline" ? rewriteOutline : job.kind === "edit" ? edit : null;
    if (run) setTimeout(() => void run(db, job).catch((e) => console.debug("[demo] decks job:", e)), 50);
  }

  const shape = (row) => ({ id: row.id, data: row, created_at: row.created_at, updated_at: row.updated_at });

  window.__LEMMA_DEMO_HANDLERS__ = {
    // A request: the job row, then its work.
    "POST pods/[^/]+/datastore/tables/decks_job/records"(input, db) {
      const fields = obj(input.data && typeof input.data === "object" ? input.data : input);
      const made = db.add("decks_job", { user_id: "user-sample", cards: null, chosen: null, before: null, ...fields });
      work(db, made);
      return shape(made);
    },
    // A job picked up again (Try again, Carry on, or the cards picked for an unsure ask).
    "PATCH pods/[^/]+/datastore/tables/decks_job/records/[^/]+"(input, db, ctx) {
      const id = decodeURIComponent(ctx.path.split("/").pop());
      const fields = obj(input.data && typeof input.data === "object" ? input.data : input);
      db.patch(id, { ...fields, updated_at: now() }, "decks_job");
      const row = db.find(id) || { id, ...fields };
      if (fields.state === "wanted") work(db, row);
      return shape(row);
    },
    // A card added to an outline: kept, so a deck's cards include it when they're written.
    "POST pods/[^/]+/datastore/tables/decks_card/records"(input, db) {
      const fields = obj(input.data && typeof input.data === "object" ? input.data : input);
      const made = db.add("decks_card", fields);
      extraCards.add(String(made.id));
      return shape(made);
    },

    // A deck from one sentence: the outliner's work. The demo opens an outline written earlier.
    decks_make(input, db) {
      const decks = db.rows("select * from decks_deck");
      const target = decks.find((d) => d.status === "outline_ready") || decks.find((d) => d.status === "ready") || decks[0];
      db.note("In the demo nothing is written from your sentence. In a Lemma, the outliner writes an outline from it for you to approve. Here's one written earlier.");
      return target ? { deck_id: target.id, message: "Opened an outline written earlier." } : { message: "The demo has no outline to open." };
    },

    // Undo: put back what an edit changed; with cards, ask again on those.
    decks_undo(input, db) {
      const job = db.find(String(input.job_id || ""));
      if (!job || job.kind !== "edit" || !["done", "failed"].includes(job.state)) return { undone: false, message: "Only a finished edit can be put back." };
      const deck = db.find(String(job.deck_id));
      const before = obj(job.before);
      if ("look" in before && deck) db.patch(deck.id, { look: before.look, voice: before.voice ?? null }, "decks_deck");
      for (const id of list(before.added)) db.patch(String(id), { deck_id: `${job.deck_id}:removed` }, "decks_card");
      const KEEP = ["title", "outline", "layout", "blocks", "notes", "status", "version", "edit_id"];
      for (const old of list(before.cards)) {
        const back = Object.fromEntries(KEEP.filter((k) => k in old).map((k) => [k, old[k]]));
        if (db.find(String(old.id))) db.patch(String(old.id), { ...back, deck_id: job.deck_id }, "decks_card");
        else {
          db.add("decks_card", { ...back, id: old.id, deck_id: job.deck_id, position: old.position || 1 });
          extraCards.add(String(old.id));
        }
      }
      const positions = obj(before.positions);
      if (Object.keys(positions).length && deck) {
        const ordered = deckCards(db, deck.id).sort((a, b) => (positions[a.id] ?? 10000) - (positions[b.id] ?? 10000) || a.position - b.position);
        renumber(db, ordered);
      }
      db.patch(job.id, { state: "undone", updated_at: now() }, "decks_job");
      if (!Array.isArray(input.cards)) return { undone: true, message: "Put back as it was." };
      const redo = db.add("decks_job", {
        user_id: "user-sample", deck_id: job.deck_id, kind: "edit", state: "wanted", request: job.request,
        action: ["rewrite", "add", "remove"].includes(job.action) ? job.action : "rewrite",
        cards: list(job.cards), chosen: input.cards, before: null,
      });
      work(db, redo);
      return { undone: true, redo_job_id: redo.id, message: "Put back, and asked again on the cards you picked." };
    },
  };
})();
