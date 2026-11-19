// What Booking does to the in-memory sample in the "Try it" demo
// (_kit/src/demo.ts), and a stand-in for the Lemma's web widget on the
// visitor's page. Nothing here leaves the page.
(function () {
  const db = () => window.__LEMMA_DEMO_DB__;
  const sample = () => window.__LEMMA_DEMO__ || { fixtures: {} };
  const PAGE_URL = "https://book.sample-lemma.example";

  // The link a member copies names the page's own address; in the demo it is a made-up one.
  window.__LEMMA_CONFIG__ = { app: { url: PAGE_URL } };

  const OPEN_COLUMNS = ["page_id", "starts_at", "name", "email", "about", "zone", "ref"];
  const widget = { id: "widget-sample", name: "Booking page", public_key: "pk_sample_demo", allowed_origins: [location.origin, PAGE_URL], answer: "anyone" };

  function page() {
    const s = db();
    return (s && s.rows("from booking_page p join booking_settings")[0]) || null;
  }
  function openTimes() {
    const p = page();
    if (!p) return { open: 0, days: 0 };
    const earliest = Date.now() + (p.notice_hours || 0) * 3600e3;
    const open = (p.slots || []).filter((s) => Date.parse(s) >= earliest);
    return { open: open.length, days: new Set(open.map((s) => s.slice(0, 10))).size };
  }

  window.__LEMMA_DEMO_HANDLERS__ = {
    // Turning the page on, step by step: each check the app reads back says yes.
    "GET pods/[^/]+/datastore/tables/booking_request": () => ({ name: "booking_request", contact_owned: true }),
    "GET pods/[^/]+/datastore/tables/booking_request/public-rows": () => ({ audience: "contacts", columns: OPEN_COLUMNS }),
    "GET pods/[^/]+/web-widgets": () => ({ items: [widget] }),
    "PATCH pods/[^/]+/web-widgets/[^/]+": () => widget,
    "GET pods/[^/]+/schedules/[^/]+": (_input, _db, ctx) => ({
      id: ctx.path.split("/").pop(), include_outside_rows: true, is_active: true, config: { when: { page_id: page() && page().id } },
    }),

    // Saving settings: the page and the hours follow at once.
    booking_save(input, d) {
      const s = d.rows("from booking_settings limit 1")[0];
      const p = page();
      if (input.day_starts && input.day_ends && input.day_starts >= input.day_ends) return { ok: false, problem: "The day has to end after it starts." };
      const keys = ["days", "day_starts", "day_ends", "minutes", "buffer_minutes", "notice_hours", "days_ahead", "max_per_day", "video", "calendars", "event_calendar"];
      const own = {};
      for (const k of keys) if (k in input) own[k] = input[k];
      if (s) d.patch(s.id, own, "booking_settings");
      if (p) {
        const shown = {};
        for (const k of ["handle", "name", "about", "more", "chat"]) if (k in input) shown[k] = input[k];
        if ("minutes" in input) shown.minutes = input.minutes;
        if ("video" in input) shown.video = input.video;
        d.patch(p.id, shown, "booking_page");
      }
      return { ok: true, message: "Saved." };
    },
    booking_slots(_input, d) {
      const p = page();
      if (p) d.patch(p.id, { slots_at: new Date().toISOString() }, "booking_page");
      return { ...openTimes(), message: "", errors: [] };
    },
    booking_needs_you: () => ({ ok: true }),
    meetings_settle: () => ({ ok: true }),

    // The sample card: a made-up visitor books, the way a real one would.
    booking_sample_data(input, d) {
      if (input.mode === "clear") return { message: "In the demo the sample stays. In a Lemma this clears the sample bookings." };
      const p = page();
      const first = p && (p.slots || []).find((s) => Date.parse(s) > Date.now() + 24 * 3600e3);
      if (!p || !first) return { message: "No open time to book." };
      d.patch(p.id, { slots: p.slots.filter((s) => s !== first) }, "booking_page");
      d.add("booking_request", {
        page_id: p.id, starts_at: first, ends_at: new Date(Date.parse(first) + (p.minutes || 30) * 60e3).toISOString().replace(/\.\d{3}Z$/, "Z"),
        name: "Ruth Adeyemi", email: "ruth@parkside-offices.example", zone: "Europe/London", state: "confirmed", sample: true,
        about: "We share an office floor with three other teams and want to try booking rooms through it.",
        reason: null, meeting_id: "meeting-sample-ruth", join_url: "https://meet.sample.example/ruth", genuine: true, kind: "customer",
        unsure: false, answers: { genuine: { value: true, confidence: 0.95 }, kind: { value: "customer", confidence: 0.86 } },
        told: null, handled_at: new Date().toISOString(), contact_id: null, meeting_kind: "customer", brief_state: "done",
        headline: "A stand-in for the brief Meetings writes on who's coming.",
        brief: "In the demo this is where the Lemma writes the brief: who Ruth is, what she's asked before, and what to have ready, from your mail and meetings.",
      });
      return { message: "Booked as a sample visitor. It's under Coming up." };
    },
  };

  // The visitor's page talks to the Lemma through its web widget (widget.js).
  // This stands in for it, on the sample page, in the page.
  const outcomes = [];
  let confirmed = false;
  const note = (text) => db() && db().note(text);
  window.Lemma = {
    async readRows(table) {
      if (table === "booking_page") {
        const p = page();
        return { rows: p ? [p] : [] };
      }
      if (table === "booking_outcome") return { rows: outcomes.filter((o) => o.at <= Date.now()) };
      return { rows: [] };
    },
    async addRow(table, values) {
      if (table !== "booking_request") return {};
      const p = page();
      if (p) db().patch(p.id, { slots: (p.slots || []).filter((s) => Date.parse(s) !== Date.parse(values.starts_at)) }, "booking_page");
      outcomes.push({ ref: values.ref, state: "confirmed", reason: "", at: Date.now() + 1800 });
      setTimeout(() => note("In a Lemma, the invite now goes on both calendars, and Meetings writes a brief on who's coming."), 2000);
      return { id: "request-" + Date.now() };
    },
    async sendCode() {
      note("In the demo no code is emailed: any six digits work.");
      return { ok: true };
    },
    async verifyCode(_email, code) {
      if (!/^\d{6}$/.test(String(code))) throw { code: "bad_code", message: "The code is 6 digits." };
      confirmed = true;
      return { ok: true };
    },
    isContact: () => confirmed,
    openChat() {
      note("In a Lemma, a chat opens here, answering from what the page says calls are for.");
    },
  };
})();
