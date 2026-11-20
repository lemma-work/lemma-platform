// What Brand kit's functions do to the in-memory sample in the "Try it" demo
// (_kit/src/demo.ts). Nothing here leaves the page.
(function () {
  const list = (v) => (Array.isArray(v) ? v : typeof v === "string" ? safe(v, []) : []);
  const obj = (v) => (v && typeof v === "object" && !Array.isArray(v) ? v : typeof v === "string" ? safe(v, {}) : {});
  function safe(text, fallback) {
    try {
      return JSON.parse(text);
    } catch {
      return fallback;
    }
  }
  const now = () => new Date().toISOString();
  const who = (input) => String(input.by_name || "").trim() || "Sam";

  const ROLE_TOKEN = {
    ink: "--brand-ink", surface: "--brand-surface", surface_2: "--brand-surface-2", line: "--brand-line", muted: "--brand-muted",
    accent: "--brand-accent", accent_ink: "--brand-accent-ink", ok: "--brand-ok", bad: "--brand-bad",
  };
  const FALLBACK = { display: "Georgia, serif", body: "system-ui, sans-serif", mono: "ui-monospace, monospace" };
  const RULE = {
    colour: "a colour that isn't the brand's", type: "a face that isn't the brand's", logo: "the logo",
    contrast: "text too faint to read", voice: "words that don't sound like the brand", other: "the brand guide",
  };
  const KIND = { logo: "A logo", icon: "An icon", photo: "A photo", illustration: "An illustration", other: "Something else" };

  function stackFor(family, role) {
    const quoted = family.includes(" ") ? `"${family}"` : family;
    return `${quoted}, ${FALLBACK[role] || FALLBACK.body}`;
  }

  /** The other designs the sample made, newest version of each kind. */
  function sampleDesign(db, kind) {
    const all = db.rows("from brand_design").filter((d) => d.state !== "archived" && d.version);
    return all.find((d) => d.kind === kind) || all[0] || null;
  }

  window.__LEMMA_DEMO_HANDLERS__ = {
    // A person's fixes: colours, faces, voice, what an asset is, the check on a
    // version, and "Looks right". The same rows the real function changes.
    brand_fix(input, db) {
      const by = who(input);
      const fixed = [];
      const brand = input.brand_id ? db.find(String(input.brand_id)) : null;

      if (brand && ((input.colors || []).length || (input.fonts || []).length || String(input.voice || "").trim())) {
        const colors = list(brand.colors).map((c) => ({ ...c }));
        const fonts = Object.fromEntries(Object.entries(obj(brand.fonts)).map(([k, v]) => [k, { ...v }]));
        const tokens = { ...obj(brand.tokens) };
        const change = {};
        for (const fix of input.colors || []) {
          const value = String(fix.value || "").trim().toLowerCase();
          const role = String(fix.role || "").trim().toLowerCase();
          if (!/^#[0-9a-f]{6}$/.test(value)) throw new Error(`${fix.value} is not a colour.`);
          const current = colors.find((c) => c.role === role && (role !== "extra" || c.name === fix.name));
          const before = current ? current.value : null;
          if (current) Object.assign(current, { value, from: `Fixed by ${by} (was ${before})` });
          else colors.push({ role, value, name: fix.name || "", for: "", from: "Your fix" });
          if (ROLE_TOKEN[role]) tokens[ROLE_TOKEN[role]] = value;
          fixed.push(`${role} ${before} → ${value}`);
        }
        for (const fix of input.fonts || []) {
          const role = String(fix.role || "").trim().toLowerCase();
          const family = String(fix.family || "").trim();
          if (!FALLBACK[role] || !family) continue;
          const before = (fonts[role] || {}).family;
          const stack = String(fix.stack || "").trim() || stackFor(family, role);
          fonts[role] = { ...(fonts[role] || {}), family, stack, source: fix.source || (fonts[role] || {}).source || "" };
          tokens[`--brand-font-${role}`] = stack;
          fixed.push(`${role} face ${before} → ${family}`);
        }
        if (String(input.voice || "").trim()) {
          change.voice = String(input.voice).trim().slice(0, 1000);
          fixed.push("voice");
        }
        db.patch(brand.id, { ...change, colors, fonts, tokens, updated_at: now() }, "brand");
      }

      if (input.asset && input.asset.id) {
        const asset = db.find(String(input.asset.id));
        if (!asset) throw new Error(`No asset ${input.asset.id}.`);
        const kind = KIND[input.asset.kind] ? input.asset.kind : asset.kind;
        const own = input.asset.own != null ? input.asset.own : asset.own == null ? true : asset.own;
        const hidden = input.asset.hidden != null ? input.asset.hidden : own === false;
        const change = {
          sorted_by: "person",
          kind,
          own,
          state: hidden ? "hidden" : "kept",
          reason: (KIND[kind] || "Something else") + (own === false ? ", not the brand's own" : "") + ` (said by ${by})`,
          updated_at: now(),
        };
        if (["primary", "inverse", "mono", "mark", "other"].includes(input.asset.variant)) change.variant = input.asset.variant;
        db.patch(asset.id, change, "brand_asset");
        fixed.push(`asset ${asset.label || asset.id}: ${kind}${own ? "" : ", not theirs"}`);
        // A logo fixed in or out changes the brand's logos.
        const owner = db.find(String(asset.brand_id));
        if (owner) {
          const before = list(owner.logos);
          const logos = before.filter((l) => l.path !== asset.file_path);
          const variant = change.variant || asset.variant || "primary";
          if (kind === "logo" && own && !hidden) logos.push({ variant, path: asset.file_path, on: variant === "inverse" ? "dark" : "light", asset_id: asset.id });
          if (JSON.stringify(logos) !== JSON.stringify(before)) {
            const primary = (logos.find((l) => l.variant === "primary") || logos[0] || {}).path || null;
            db.patch(owner.id, { logos, logo_path: primary }, "brand");
          }
        }
      }

      if (input.version && input.version.id) {
        const version = db.find(String(input.version.id));
        if (!version) throw new Error(`No version ${input.version.id}.`);
        const follows = !!input.version.follows;
        const broke = follows ? "none" : RULE[input.version.broke] ? input.version.broke : "other";
        const reason = follows ? `Follows the brand (said by ${by}).` : `Breaks the brand: ${RULE[broke]} (said by ${by}).`;
        db.patch(version.id, { verdict: follows ? "follows" : "breaks", broke, reason, judged_by: "person", updated_at: now() }, "brand_version");
        const design = db.find(String(version.design_id));
        if (design && Number(design.version) === Number(version.version) && (design.state === "review" || design.state === "off_brand")) {
          db.patch(design.id, { state: follows ? "review" : "off_brand" }, "brand_design");
        }
        fixed.push(`version ${version.version}: ${follows ? "follows" : "breaks"}`);
      }

      if (input.looks_right && brand) {
        db.patch(brand.id, { state: "ready", checked_at: now(), checked_by: by }, "brand");
        fixed.push("checked");
      }
      return { fixed, message: fixed.length ? "Fixed: " + fixed.join("; ") : "Nothing to fix." };
    },

    // Approve, put away, bring back: rows only. A new design or a change is
    // the Lemma's assistant making something, which the demo doesn't do.
    brand_design_action(input, db) {
      const action = String(input.action || "").toLowerCase();
      const by = who(input);
      if (action === "new") {
        const shown = sampleDesign(db, input.kind);
        db.note("In the demo nothing new is made. In a Lemma, its assistant makes this in the brand and checks it before you see it. Here's one it made earlier.");
        return { design_id: shown ? shown.id : "", state: shown ? shown.state : "", message: "Opened a design the sample made." };
      }
      const design = db.find(String(input.design_id || ""));
      if (!design) throw new Error(`No design ${input.design_id}.`);
      if (action === "change") {
        db.note("In the demo no new version is made. In a Lemma, its assistant makes the next one from your change and checks it against the brand.");
        return { design_id: design.id, state: design.state, message: "Kept your change." };
      }
      if (action === "approve") {
        db.patch(design.id, { state: "approved", approved_at: now(), approved_by: by, updated_at: now() }, "brand_design");
        return { design_id: design.id, state: "approved", message: "Approved." };
      }
      if (action === "archive") {
        db.patch(design.id, { state: "archived", updated_at: now() }, "brand_design");
        return { design_id: design.id, state: "archived", message: "Put away." };
      }
      if (action === "restore") {
        const last = db.rows("select id, design_id, version").find((v) => v.design_id === design.id && Number(v.version) === Number(design.version));
        const state = !last ? "failed" : last.verdict === "breaks" ? "off_brand" : "review";
        db.patch(design.id, { state, updated_at: now() }, "brand_design");
        return { design_id: design.id, state, message: "Brought back." };
      }
      throw new Error("action is new, change, approve, archive or restore.");
    },

    // Read again: the brand is read for a moment, then comes back as it was
    // (the made-up site never changes), waiting for a check.
    brand_read(input, db) {
      const brand = input.brand_id ? db.find(String(input.brand_id)) : null;
      if (!brand) {
        db.note("In the demo nothing is read. In a Lemma, its assistant reads your site or files into a brand for you to check.");
        return { message: "Nothing read in the demo." };
      }
      const job = db.add("brand_job", {
        kind: "read",
        state: "working",
        brand_id: brand.id,
        source: "website",
        source_ref: brand.site_url || "",
        activity: "Looking at the site's colours, type and logos",
        brand_state_before: brand.state,
        requested_by: "user-sample",
        requested_name: who(input),
        started_at: now(),
      });
      db.patch(brand.id, { state: "reading" }, "brand");
      setTimeout(() => {
        db.patch(job.id, { state: "done", activity: "Read. Waiting for a person to check it.", finished_at: now() }, "brand_job");
        db.patch(brand.id, { state: "check", synced_at: now(), checked_at: null, checked_by: null }, "brand");
        db.note("In the demo the made-up site never changes, so the brand comes back as it was. In a Lemma, it's read again and you check what changed.");
      }, 3200);
      return { brand_id: brand.id, job_id: job.id, message: "Reading it again." };
    },
  };
})();
