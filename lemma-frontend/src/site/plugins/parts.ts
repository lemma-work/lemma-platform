/**
 * What each plugin is made of, read from the bundle it starts from on
 * 17 Nov 2026: its tables and their key columns, its apps and their screens,
 * its agents, functions, workflows and schedules, the accounts it asks for and
 * its skills. Data, not copy: the plugin pages render it as it is, so a change
 * to a bundle is a change here. What a plugin still lacks lives beside it in
 * `catalog.ts`.
 *
 * `eachPersonsOwn` is row-level security: every person sees only their rows.
 * `kind` is the shared table a bundle table holds, if any (see `tables.ts`).
 * A connector with `declared: false` is used by the bundle's code but not
 * declared under its `connectors/`, so an install never asks for it.
 */

import type { BundleParts, PluginSlug } from "./types";

export const PARTS: Partial<Record<PluginSlug, BundleParts>> = {
    "people-map": {
        tables: [
            {
                name: "activity",
                kind: null,
                eachPersonsOwn: true,
                columns: 5,
                key: ["kind", "text", "thread_id", "ref_id"],
            },
            {
                name: "commitment",
                kind: "commitment",
                eachPersonsOwn: true,
                columns: 8,
                key: ["direction", "text", "quote", "due_at", "state"],
            },
            {
                name: "company",
                kind: "company",
                eachPersonsOwn: true,
                columns: 17,
                key: ["domain", "name", "description", "industry"],
            },
            {
                name: "draft",
                kind: null,
                eachPersonsOwn: true,
                columns: 15,
                key: ["mailbox_id", "kind", "subject", "body", "state"],
            },
            {
                name: "mailbox",
                kind: null,
                eachPersonsOwn: true,
                columns: 10,
                key: ["provider", "account_id", "email", "state"],
            },
            {
                name: "meeting",
                kind: "meeting",
                eachPersonsOwn: true,
                columns: 15,
                key: ["provider", "provider_event_id", "title", "start_at", "status", "brief_state"],
            },
            {
                name: "memory",
                kind: null,
                eachPersonsOwn: true,
                columns: 6,
                key: ["about", "fact", "state"],
            },
            {
                name: "message",
                kind: "touch",
                eachPersonsOwn: true,
                columns: 15,
                key: [
                    "mailbox_id",
                    "provider_message_id",
                    "provider_thread_id",
                    "direction",
                    "subject",
                    "tier",
                ],
            },
            {
                name: "person",
                kind: "person",
                eachPersonsOwn: true,
                columns: 28,
                key: ["email", "name", "title", "warmth"],
            },
            {
                name: "profile",
                kind: null,
                eachPersonsOwn: true,
                columns: 12,
                key: ["onboarding_step", "display_name", "timezone", "my_domains"],
            },
            {
                name: "rule",
                kind: null,
                eachPersonsOwn: true,
                columns: 6,
                key: ["scope", "text", "state"],
            },
            {
                name: "split",
                kind: null,
                eachPersonsOwn: true,
                columns: 10,
                key: ["key", "name", "rule", "kind", "state"],
            },
            {
                name: "sync_state",
                kind: null,
                eachPersonsOwn: true,
                columns: 11,
                key: ["mailbox_id", "kind", "state"],
            },
            {
                name: "thread",
                kind: "touch",
                eachPersonsOwn: true,
                columns: 34,
                key: ["mailbox_id", "provider", "provider_thread_id", "subject", "tier", "state"],
            },
        ],
        apps: [
            {
                name: "today",
                title: "Today",
                shape: "queue",
                line: "What needs you today: replies owed, promises both ways, meetings with a brief, deal changes to review, people who went quiet.",
                screens: [
                    "Today: replies owed, promises both ways, meetings, deal changes, people gone quiet",
                    "Set-up: connect mail, or look around with sample data",
                ],
                inBundle: true,
                cover: false,
            },
            {
                name: "people",
                title: "People",
                shape: "list",
                line: "Everyone you deal with, built from your mail and calendar, and the page for each: where things stand, what is owed, everything that happened.",
                screens: [
                    "Everyone, with search and filters",
                    "A person's page: where things stand, what's owed, everything that happened",
                ],
                inBundle: true,
                cover: false,
            },
            {
                name: "companies",
                title: "Companies",
                shape: "list",
                line: "The companies of the people you deal with: what they do, who you know there, the deals and the conversations.",
                screens: [
                    "Every company, with search",
                    "A company's page: who you know there, its deals, what it does",
                ],
                inBundle: true,
                cover: false,
            },
        ],
        agents: [
            {
                name: "assistant",
                line: "Answers questions about your mail, finds threads, writes and adjusts splits and rules, asks for drafts, snoozes and files threads.",
            },
            {
                name: "briefer",
                line: "Writes a one-page brief before a meeting with outside people: who they are, where you left off, open promises, what they will want, what you should get out of it.",
            },
            {
                name: "researcher",
                line: "Looks up one company on the web: what they do, how big, where, and anything recent worth knowing before you talk to them.",
            },
            {
                name: "summarizer",
                line: "Writes 'where things stand' with one person in two to four sentences, each citing the conversation or meeting it came from.",
            },
            {
                name: "triage",
                line: "Reads a batch of mail threads for one person: what each needs from them, promises both ways, follow-up times, and what the conversation says about a deal.",
            },
            {
                name: "writer",
                line: "Drafts one reply, nudge or scheduling email in the person's own voice from a prepared brief.",
            },
        ],
        functions: [
            {
                name: "apply_triage",
                line: "Write the triage agent's decisions, validated and batched: thread state, promises, facts, VIP proposals, deal signals to review, and company names.",
            },
            {
                name: "calendar_sync",
                line: "Read next week's meetings from every connected Google or Outlook calendar, mark the ones with outside people, and ask for a brief when one is within a day.",
            },
            {
                name: "company_context",
                line: "Gather what the mail already knows about one company, so the researcher looks up only what is missing.",
            },
            {
                name: "ensure_profile",
                line: "Create the caller's profile and default splits if they are missing.",
            },
            {
                name: "mail_sync",
                line: "Read Gmail and Outlook into threads, messages and one searchable document per conversation.",
            },
            {
                name: "meeting_context",
                line: "Gather what your mail knows about the outside people on one meeting: who they are, recent threads, newest words, open promises.",
            },
            {
                name: "people_refresh",
                line: "Recompute who you deal with and the companies they work at, from your own mail and calendar: counts, cadence, warmth, next meeting, and the health of the team's open deals.",
            },
            {
                name: "person_context",
                line: "Gather what the mail knows about one person as numbered sources: newest conversations and meetings, open promises both ways, facts and deals.",
            },
            {
                name: "sample_data",
                line: "Load or clear eight made-up conversations, two meetings and four deals, so the CRM can be seen working before any mail is connected.",
            },
            {
                name: "save_brief",
                line: "Store a meeting brief and link the threads it drew on.",
            },
            {
                name: "save_draft",
                line: "Store the writer's draft (or its reason for not writing one) and mark the thread's draft ready.",
            },
            {
                name: "save_person_summary",
                line: "Store where things stand with one person, keeping only the sources the text cites.",
            },
            {
                name: "save_research",
                line: "Store what the researcher found on one company, keeping any name the person typed.",
            },
            {
                name: "search_mail",
                line: "Search your own conversations (meaning and keywords) and return matching threads with excerpts.",
            },
            {
                name: "send_draft",
                line: "Deliver one approved draft from the mailbox its thread belongs to, in its thread.",
            },
            {
                name: "sweep",
                line: "The hourly pass: bring snoozed threads back, turn due follow-ups into ready nudges, and list approved drafts whose send time came.",
            },
            {
                name: "thread_action",
                line: "Do one thing to one or more threads (done, restore, read, unread, snooze, unsnooze, mute, state, split, draft) here and, where it applies, in Gmail or Outlook.",
            },
            {
                name: "triage_packets",
                line: "Everything the triage agent needs for one batch in one object: the newest words of each thread, the sender's history, your splits and rules, the pipeline, and the team's open deals with these companies.",
            },
            {
                name: "triage_queue",
                line: "Claim the threads waiting for a decision and cut them into batches for the triage agent.",
            },
            {
                name: "writing_context",
                line: "Gather everything the writer needs for one draft: voice, rules, the thread, who to reply to, what is known about the other side, past lines to them, open promises, and free times when the ask is to meet.",
            },
        ],
        workflows: [
            {
                name: "catch-up",
                line: "Every 15 minutes: read anything new in every mailbox (and keep a first read going), sort what arrived, then bring people, companies and deal health up to date.",
                starts: "On a timer",
            },
            {
                name: "company-research",
                line: "Look one company up on the web and store what it does, its size, where it is and anything recent.",
                starts: "When a row changes",
            },
            {
                name: "meeting-brief",
                line: "Write the brief for one upcoming meeting with outside people.",
                starts: "When a row changes",
            },
            {
                name: "nightly",
                line: "Nightly: recompute who you deal with from your mail.",
                starts: "On a timer",
            },
            {
                name: "on-gmail-received",
                line: "New Gmail: read it within a minute or two and sort it, instead of waiting for the catch-up.",
                starts: "When mail arrives or is sent",
            },
            {
                name: "on-gmail-sent",
                line: "Gmail you sent: read it within a minute or two and sort it, instead of waiting for the catch-up.",
                starts: "When mail arrives or is sent",
            },
            {
                name: "on-outlook-received",
                line: "New Outlook mail: read it within a minute or two and sort it, instead of waiting for the catch-up.",
                starts: "When mail arrives or is sent",
            },
            {
                name: "on-outlook-sent",
                line: "Outlook mail you sent: read it within a minute or two and sort it, instead of waiting for the catch-up.",
                starts: "When mail arrives or is sent",
            },
            {
                name: "person-summary",
                line: "Write where things stand with one person, citing the conversations and meetings it rests on.",
                starts: "When a row changes",
            },
            {
                name: "sweep",
                line: "Hourly: bring back snoozed threads, ready nudges for follow-ups that came due, refresh next week's meetings, send drafts scheduled for now.",
                starts: "On a timer",
            },
            {
                name: "triage",
                line: "Sort the threads waiting for a decision: claim batches, judge each with the triage agent, write the results.",
                starts: "By hand",
            },
            {
                name: "write-draft",
                line: "Write the draft a thread asked for: gather the brief, let the writer draft it, store it beside the thread.",
                starts: "When a row changes",
            },
        ],
        schedules: [
            {
                name: "brief-wanted",
                when: "When a meeting row's brief_state becomes wanted",
                runs: "meeting-brief",
            },
            {
                name: "draft-wanted",
                when: "When a thread row's draft_state becomes wanted",
                runs: "write-draft",
            },
            {
                name: "research-wanted",
                when: "When a company row's research_state becomes wanted",
                runs: "company-research",
            },
            {
                name: "summary-wanted",
                when: "When a person row's summary_state becomes wanted",
                runs: "person-summary",
            },
        ],
        connectors: [
            {
                service: "Gmail",
                mode: "PER_MEMBER",
                required: false,
                why: "Reads your mail for the people you deal with and what is owed, and sends a reply only when you press Send.",
                declared: true,
            },
            {
                service: "Google Calendar",
                mode: "PER_MEMBER",
                required: false,
                why: "Reads your meetings, so people you meet count and each meeting with someone outside gets a brief.",
                declared: true,
            },
            {
                service: "Outlook",
                mode: "PER_MEMBER",
                required: false,
                why: "Reads your Outlook mail and calendar for the people you deal with and what is owed, and sends a reply only when you press Send.",
                declared: true,
            },
        ],
        skills: [],
    },
    "deal-tracker": {
        tables: [
            {
                name: "deal",
                kind: "deal",
                eachPersonsOwn: false,
                columns: 19,
                key: ["name", "stage", "amount", "close_date", "health"],
            },
            {
                name: "signal",
                kind: null,
                eachPersonsOwn: true,
                columns: 15,
                key: ["kind", "quote", "state"],
            },
            {
                name: "stage",
                kind: null,
                eachPersonsOwn: false,
                columns: 4,
                key: ["name", "position", "kind"],
                seed: {
                    rows: 5,
                    ids: true,
                },
            },
        ],
        apps: [
            {
                name: "deals",
                title: "Deals",
                shape: "board",
                line: "The team pipeline as a board you can drag, with changes read from conversations waiting for a yes.",
                screens: [
                    "Board by stage, drag to move",
                    "The same deals as a table",
                    "A deal's page with its history and proposed changes",
                ],
                inBundle: true,
                cover: false,
            },
        ],
        agents: [],
        functions: [],
        workflows: [],
        schedules: [],
        connectors: [],
        skills: [],
    },
    "deck-maker": {
        tables: [
            {
                name: "cards",
                kind: null,
                eachPersonsOwn: false,
                columns: 8,
                key: ["deck_id", "position", "title", "status"],
            },
            {
                name: "decks",
                kind: null,
                eachPersonsOwn: false,
                columns: 10,
                key: ["title", "topic", "status"],
            },
            {
                name: "themes",
                kind: null,
                eachPersonsOwn: false,
                columns: 4,
                key: ["name", "description", "tokens", "image_keywords"],
            },
        ],
        apps: [
            {
                name: "decks",
                title: "Decks",
                shape: "deck",
                line: "Decks from a topic: outline, card-by-card writing and editing by asking; exports PowerPoint.",
                screens: [
                    "Your decks",
                    "Outline to approve",
                    "Editor with the agent panel",
                    "Present",
                    "Print",
                ],
                inBundle: false,
                cover: false,
            },
        ],
        agents: [
            {
                name: "card-writer",
                line: "Generates full card content for an approved deck outline, writing one card at a time so the UI can stream progress, then marks the deck ready.",
            },
            {
                name: "deck-editor",
                line: "Conversational deck-wide editing agent (the 'agent panel' in the editor): restyle, retone, reorder, add/remove cards, switch themes via natural language.",
            },
            {
                name: "outline-writer",
                line: "Generates a deck outline from a topic: creates one card record per outline point and marks the deck outline_ready for human approval.",
            },
            {
                name: "pptist-outline",
                line: "Generates a markdown presentation outline in PPTist's format (# title / ## chapter / ### section / - points).",
            },
            {
                name: "pptist-slides",
                line: "Turns a markdown outline into PPTist AIPPT slide JSON, streamed one JSON object per line.",
            },
            {
                name: "pptist-writer",
                line: "Rewrites a snippet of slide text per a user command (expand, shorten, polish, translate).",
            },
        ],
        functions: [],
        workflows: [],
        schedules: [],
        connectors: [],
        skills: [],
    },
    "brand-kit": {
        tables: [
            {
                name: "brand_assets",
                kind: null,
                eachPersonsOwn: false,
                columns: 9,
                key: ["brand_id", "kind", "variant", "label", "approved"],
            },
            {
                name: "brands",
                kind: "brand",
                eachPersonsOwn: false,
                columns: 11,
                key: ["name", "slug", "website_url", "status", "source_kind", "is_default"],
            },
            {
                name: "design_versions",
                kind: null,
                eachPersonsOwn: false,
                columns: 6,
                key: ["design_id", "version", "artifact_path", "preview_path"],
            },
            {
                name: "designs",
                kind: null,
                eachPersonsOwn: false,
                columns: 12,
                key: ["brand_id", "title", "kind", "status", "version"],
            },
            {
                name: "intake_jobs",
                kind: null,
                eachPersonsOwn: false,
                columns: 9,
                key: ["source_kind", "status", "brand_id", "source_ref"],
            },
        ],
        apps: [
            {
                name: "brand-studio",
                title: "Brand studio",
                shape: "canvas",
                line: "Get a brand, then make things on it.",
                screens: [
                    "Start",
                    "Intake: reading your website",
                    "Brand kit: colours, type, logos and voice, each with its source",
                    "Studio: make a piece",
                    "Library: everything made on the brand",
                ],
                inBundle: true,
                cover: false,
            },
        ],
        agents: [],
        functions: [
            {
                name: "extract_site_style",
                line: "Fetch a public web page and return ranked brand candidates: CSS custom properties, colours, font stacks, radii, logo and icon URLs, plus a warning when the page is JS-rendered.",
            },
            {
                name: "start_intake",
                line: "Open brand acquisition: upsert the brand row and queue an intake job in one step, so a half-created brand is never observable.",
            },
        ],
        workflows: [],
        schedules: [
            {
                name: "design-requested",
                when: "When a designs row is added or changed",
                runs: "the Lemma's assistant",
            },
            {
                name: "intake-started",
                when: "When a intake_jobs row is added or changed",
                runs: "the Lemma's assistant",
            },
        ],
        connectors: [],
        skills: [],
    },
    "content-calendar": {
        tables: [
            {
                name: "adaptations",
                kind: "content",
                eachPersonsOwn: false,
                columns: 10,
                key: ["idea_id", "channel", "content", "status"],
            },
            {
                name: "captures",
                kind: null,
                eachPersonsOwn: false,
                columns: 8,
                key: ["source", "status", "raw_text", "audio_path"],
            },
            {
                name: "drafts",
                kind: "content",
                eachPersonsOwn: false,
                columns: 5,
                key: ["idea_id", "version", "content", "status"],
            },
            {
                name: "findings",
                kind: null,
                eachPersonsOwn: false,
                columns: 8,
                key: ["idea_id", "claim", "quote", "origin", "status"],
            },
            {
                name: "idea_links",
                kind: null,
                eachPersonsOwn: false,
                columns: 5,
                key: ["from_idea_id", "to_idea_id", "relation"],
            },
            {
                name: "ideas",
                kind: "content",
                eachPersonsOwn: false,
                columns: 11,
                key: ["title", "one_liner", "status"],
            },
            {
                name: "suggestions",
                kind: null,
                eachPersonsOwn: false,
                columns: 6,
                key: ["kind", "title", "body", "status"],
            },
        ],
        apps: [
            {
                name: "studio",
                title: "Studio",
                shape: "board",
                line: "Ideas from capture to published, with research, drafts and per-channel versions.",
                screens: ["Today", "Pipeline by stage", "Workbench for one idea", "Voice", "Chat"],
                inBundle: false,
                cover: false,
            },
        ],
        agents: [
            {
                name: "capture-agent",
                line: "Distills raw captures into idea candidates, files corpus copies, links them to past thinking, and stages them for human triage.",
            },
            {
                name: "distribution-agent",
                line: "Adapts an approved draft into channel-native LinkedIn, X, reel-script, and newsletter versions for per-channel approval.",
            },
            {
                name: "draft-agent",
                line: "Writes draft versions from the chosen angle, kept findings, and the voice doc; applies send-back notes and records learned preferences.",
            },
            {
                name: "research-agent",
                line: "Gathers corpus and web evidence for an idea, maps the white space, and proposes angle options for the human to pick.",
            },
            {
                name: "scout-agent",
                line: "Weekly background scout: detects storylines circled over months, timely resurrections of dormant ideas, and position shifts — always with quoted receipts.",
            },
            {
                name: "studio-agent",
                line: "Chat companion beside the studio: reads everything, pushes back on hooks and drafts, fetches sources.",
            },
        ],
        functions: [],
        workflows: [],
        schedules: [],
        connectors: [],
        skills: [],
    },
    "user-feedback": {
        tables: [
            {
                name: "actions",
                kind: null,
                eachPersonsOwn: false,
                columns: 15,
                key: ["product_id", "change", "status"],
            },
            {
                name: "features",
                kind: "feature",
                eachPersonsOwn: false,
                columns: 6,
                key: ["name", "origin", "status"],
            },
            {
                name: "jobs",
                kind: null,
                eachPersonsOwn: false,
                columns: 13,
                key: ["search_id", "kind", "dataset_id", "status"],
            },
            {
                name: "opinions",
                kind: "feedback",
                eachPersonsOwn: false,
                columns: 16,
                key: ["post_id", "product_id", "quote", "polarity", "cohort", "authenticity"],
            },
            {
                name: "posts",
                kind: "mention",
                eachPersonsOwn: false,
                columns: 28,
                key: ["url", "title", "status", "authenticity"],
            },
            {
                name: "products",
                kind: null,
                eachPersonsOwn: false,
                columns: 10,
                key: ["name", "role", "brand", "category"],
            },
            {
                name: "searches",
                kind: null,
                eachPersonsOwn: false,
                columns: 19,
                key: ["product_id", "platforms", "trigger", "status"],
            },
            {
                name: "settings",
                kind: null,
                eachPersonsOwn: false,
                columns: 2,
                key: ["key", "value"],
            },
            {
                name: "shifts",
                kind: null,
                eachPersonsOwn: false,
                columns: 12,
                key: ["product_id", "kind", "headline", "status"],
            },
        ],
        apps: [
            {
                name: "library",
                title: "Library",
                shape: "feed",
                line: "The library of what people say about your product and its rivals: set up what to listen for, connect Bright Data, then read every opinion by product and feature with its source.",
                screens: [
                    "Set-up: what to listen for",
                    "Wall of opinions",
                    "Features, ranked by what people say",
                    "A post with its opinions and source",
                    "Report",
                    "Listening runs",
                ],
                inBundle: true,
                cover: false,
            },
            {
                name: "spec",
                title: "Bright Data spec",
                shape: "page",
                line: "Public OpenAPI description of the Bright Data calls the Lemma makes, read by the brightdata-api connector install.",
                screens: [
                    "No screen: a public description of the calls the Lemma makes, read when the connection is installed",
                ],
                inBundle: true,
                cover: false,
            },
        ],
        agents: [
            {
                name: "analyst",
                line: "Writes a product's findings from the opinions, with numbers and exact quotes, and proposes actions with a team and a KPI.",
            },
            {
                name: "extractor",
                line: "Reads one collected post — body, transcript, comments — and returns every opinion in it: the exact words, the product, the feature, liked or disliked, who said it, and where in the video.",
            },
            {
                name: "scout",
                line: "Given a product name, finds what it is, what people call it, the rivals people compare it with, and the features people judge it on — so setup is ticking boxes, not typing.",
            },
            {
                name: "screener",
                line: "Looks at each search result's title and snippet and keeps the ones likely to hold opinions about the product; sets the rest aside with a reason a person can check.",
            },
        ],
        functions: [
            {
                name: "apply_screen",
                line: "Write the screener's keep or set-aside call (with its reason) onto each post the run found, and return the posts to read.",
            },
            {
                name: "check_brightdata",
                line: "Check a Bright Data API key server-side (a bad key's 401 would otherwise sign the person out of the app).",
            },
            {
                name: "finish_search",
                line: "Close a listening run once its last post is read, and record shifts: features whose disliked share over the last two weeks sits well above the three months before.",
            },
            {
                name: "load_post",
                line: "Read one post's collected document and the products and features the extractor maps opinions to.",
            },
            {
                name: "poll_jobs",
                line: "Every minute: check running Bright Data collections, download the finished ones into posts with a readable document each (description, transcript, comments), and move complete listens to screening.",
            },
            {
                name: "queue_listen",
                line: "Queue a listen for every active product, skipping products that already have a run in progress.",
            },
            {
                name: "save_opinions",
                line: "Write the extractor's opinions for one post: map product and feature names to rows, add features the audience raised, carry the post's authenticity onto every opinion.",
            },
            {
                name: "screen_input",
                line: "Give the screener a listen's found posts (title and the start of the description) with the products the Lemma tracks.",
            },
            {
                name: "start_discovery",
                line: "Start a listen: one Bright Data keyword discovery per source (YouTube, TikTok, Reddit), recorded in jobs for poll_jobs to collect.",
            },
        ],
        workflows: [
            {
                name: "listen",
                line: "One listen: Bright Data searches YouTube, TikTok and Reddit by keyword and follows the product's X and LinkedIn accounts; then check every 40 seconds until the results are in.",
                starts: "When a row changes",
            },
            {
                name: "poll",
                line: "Every 15 minutes: a safety net that collects any Bright Data search a listen stopped watching, and closes listens stuck mid-read.",
                starts: "On a timer",
            },
            {
                name: "read_post",
                line: "Read one kept post: load its document, pull out its opinions, save them, and close the listen if it was the last post.",
                starts: "When a row changes",
            },
            {
                name: "screen",
                line: "When a listen's posts are all in: set aside the ones about other products, and send the rest to be read.",
                starts: "When a row changes",
            },
            {
                name: "weekly",
                line: "Queue a fresh listen for every product the Lemma still listens to.",
                starts: "On a timer",
            },
        ],
        schedules: [
            {
                name: "on-collected",
                when: "When a posts row's status becomes collected",
                runs: "read_post",
            },
            {
                name: "on-screening",
                when: "When a searches row's status becomes screening",
                runs: "screen",
            },
            {
                name: "on-search",
                when: "When a searches row is added or changed",
                runs: "listen",
            },
            {
                name: "poll-sweep",
                when: "Every 15 minutes",
                runs: "poll",
            },
            {
                name: "weekly-listen",
                when: "Mondays at 07:00",
                runs: "weekly",
            },
        ],
        connectors: [
            {
                service: "Bright Data",
                mode: "SHARED",
                required: true,
                why: "Searches YouTube, TikTok and Reddit and follows X and LinkedIn accounts. A key you paste, through a custom connection.",
                declared: false,
            },
        ],
        skills: [],
    },
    "pr-review": {
        tables: [
            {
                name: "announcement",
                kind: null,
                eachPersonsOwn: false,
                columns: 7,
                key: ["key", "kind", "title", "speech"],
            },
            {
                name: "automation",
                kind: null,
                eachPersonsOwn: false,
                columns: 13,
                key: ["name", "title", "core"],
            },
            {
                name: "finding",
                kind: null,
                eachPersonsOwn: false,
                columns: 22,
                key: ["pr", "fingerprint", "source", "severity", "title", "status"],
            },
            {
                name: "guideline",
                kind: null,
                eachPersonsOwn: false,
                columns: 15,
                key: ["slug", "text", "severity", "status", "check"],
            },
            {
                name: "notice",
                kind: null,
                eachPersonsOwn: false,
                columns: 13,
                key: ["key", "kind", "subject", "channel", "text", "status"],
            },
            {
                name: "person",
                kind: null,
                eachPersonsOwn: false,
                columns: 8,
                key: ["name", "email", "reviews", "nudge"],
            },
            {
                name: "point",
                kind: null,
                eachPersonsOwn: false,
                columns: 14,
                key: ["key", "pr", "kind", "ref", "title", "call"],
            },
            {
                name: "pull_request",
                kind: "pr",
                eachPersonsOwn: false,
                columns: 42,
                key: ["key", "repo", "number", "title", "state", "draft"],
            },
            {
                name: "repo",
                kind: "repo",
                eachPersonsOwn: false,
                columns: 6,
                key: ["full_name", "review_mode", "active"],
            },
            {
                name: "review",
                kind: null,
                eachPersonsOwn: false,
                columns: 27,
                key: ["pr", "sha", "status", "risk", "verdict"],
            },
            {
                name: "risk_rule",
                kind: null,
                eachPersonsOwn: false,
                columns: 6,
                key: ["pattern", "tier", "reason", "status"],
            },
            {
                name: "screenshot",
                kind: null,
                eachPersonsOwn: false,
                columns: 14,
                key: ["taken_by", "page", "device", "theme", "phase", "path"],
            },
            {
                name: "setting",
                kind: null,
                eachPersonsOwn: false,
                columns: 3,
                key: ["key", "value", "updated_by"],
            },
            {
                name: "signal",
                kind: "signal",
                eachPersonsOwn: false,
                columns: 22,
                key: ["number", "source", "external_id", "title", "status", "kind"],
            },
            {
                name: "task",
                kind: null,
                eachPersonsOwn: false,
                columns: 19,
                key: ["number", "kind", "title", "repo", "worktree", "status"],
            },
        ],
        apps: [
            {
                name: "factory",
                title: "Factory",
                shape: "list",
                line: "Pull requests sorted by who has to act: each one reduced to the decisions it makes, the guidelines it was checked against, and the findings that need a call.",
                screens: [
                    "Pull requests by who has to act",
                    "A pull request: decisions, guidelines checked, findings",
                    "Issues",
                    "Tasks",
                    "Guidelines",
                    "Automations",
                    "Settings",
                ],
                inBundle: true,
                cover: false,
            },
            {
                name: "review",
                title: "Review",
                shape: "cards",
                line: "Pull request review, one card at a time: every key change and every finding the reviewer raised is a card to call good to go, not OK, or take to a task.",
                screens: [
                    "Queue",
                    "One card at a time: good to go, not OK, or make it a task",
                ],
                inBundle: true,
                cover: false,
            },
        ],
        agents: [
            {
                name: "builder",
                line: "Does one task a person started — something new, an issue to fix, or a finding on an open PR — in its own worktree, and opens (or pushes to) a pull request.",
            },
            {
                name: "fixer",
                line: "Fixes one finding a person marked 'Fix it': the smallest change, pushed as a new commit to the PR's own branch.",
            },
            {
                name: "nudger",
                line: "Tells a person when a pull request starts waiting on them, sends the morning roundup of what has waited too long, and writes Friday's note on how the week went.",
            },
            {
                name: "reviewer",
                line: "Reviews one pull request at one commit as a story: before and after, with a diagram, what it enables and fixes, what could break across nine failure types, and the calls only a person can make.",
            },
            {
                name: "scout",
                line: "Reads a repository and its recent pull requests during setup, then proposes guidelines, risk paths, people and the reasons behind each automation.",
            },
            {
                name: "triager",
                line: "Sizes one incoming issue: what it is, how bad, how many people it reaches (asking PostHog, never guessing), and where in the code it probably lives.",
            },
            {
                name: "updater",
                line: "Updates the last review of a pull request after a push: reads only the files that moved (or what main changed under them), checks what the last review said about them, and returns only the sections that changed.",
            },
        ],
        functions: [
            {
                name: "announce",
                line: "Say what changed where people already are: a pull request opened, reviewed or merged, an issue triaged or fixed.",
            },
            {
                name: "begin_review",
                line: "Start one review round: claim the queued PR, open a review row, and gather what the reviewer needs — guidelines in scope, earlier findings, and conflicts with other open PRs, found by code.",
            },
            {
                name: "check_public",
                line: "Reads a pull request the way a stranger would and flags anything that should not be public.",
            },
            {
                name: "intake",
                line: "Bring pull requests into the factory: read them from GitHub, set risk and reviewer by code, and queue a review when one is due.",
            },
            {
                name: "poll_sources",
                line: "Collect what the outside world says is wrong: new GitHub issues on watched repos, messages in the watched Slack channel, and PostHog error issues.",
            },
            {
                name: "recover_reviews",
                line: "Close out reviews whose workflow run ended without saving: save the answer its session finished with, or mark it failed so the PR offers Review again.",
            },
            {
                name: "save_review",
                line: "Write one finished review round: the review row, findings matched by fingerprint (a dismissed finding never comes back), and the PR's new state.",
            },
            {
                name: "settle",
                line: "Recompute where a PR stands after a review or a person's call — the verdict guard, who it waits on — and publish the factory/guidelines check on GitHub.",
            },
        ],
        workflows: [
            {
                name: "announce-issue",
                line: "Post an issue's triage to Slack and GitHub, as Settings -> Notifications allows.",
                starts: "When a row changes",
            },
            {
                name: "announce-pr",
                line: "Post a pull request's news to Slack and GitHub, as Settings -> Notifications allows.",
                starts: "When a row changes",
            },
            {
                name: "announce-task",
                line: "Says a task's news: when a builder starts, when its pull request opens, or that it failed.",
                starts: "When a row changes",
            },
            {
                name: "check-pr-public",
                line: "Check a task's pull request for internal data before anyone reads it.",
                starts: "When a row changes",
            },
            {
                name: "poll",
                line: "Collect new issues from GitHub, the watched Slack channel and PostHog.",
                starts: "On a timer",
            },
            {
                name: "pr-event",
                line: "A pull request opened, moved or closed on GitHub: bring it in and queue a review if one is due.",
                starts: "When GitHub reports a change",
            },
            {
                name: "review-pr",
                line: "Review one queued pull request: claim it, read it (all of it, or only what moved since the last review), store what was found, settle who it waits on.",
                starts: "When a row changes",
            },
            {
                name: "settle-carried",
                line: "A review was carried to a new head without a run (only main moved): settle the PR and put the GitHub check on the new head, so branch protection sees it.",
                starts: "When a row changes",
            },
            {
                name: "settle-pr",
                line: "After a finding is decided, recompute the PR's verdict and who it waits on, and update the GitHub check.",
                starts: "When a row changes",
            },
            {
                name: "sync",
                line: "Re-read every open pull request on the watched repos, settle what changed, and close out reviews whose run ended without saving.",
                starts: "On a timer",
            },
        ],
        schedules: [
            {
                name: "finding-decided",
                when: "When a finding row's decided_by is set",
                runs: "settle-pr",
            },
            {
                name: "friday-scorecard",
                when: "Fridays at 16:00",
                runs: "nudger",
            },
            {
                name: "issue-triaged",
                when: "When a signal row's status becomes triaged",
                runs: "announce-issue",
            },
            {
                name: "morning-roundup",
                when: "Weekdays at 10:00",
                runs: "nudger",
            },
            {
                name: "poll-sources",
                when: "Every 15 minutes",
                runs: "poll",
            },
            {
                name: "pr-carried",
                when: "When a pull_request row's carried_at changes",
                runs: "settle-carried",
            },
            {
                name: "pr-merged",
                when: "When a pull_request row's state becomes merged",
                runs: "announce-pr",
            },
            {
                name: "pr-opened",
                when: "When a pull_request row is open",
                runs: "announce-pr",
            },
            {
                name: "pr-reviewed",
                when: "When a pull_request row's reviewed_sha changes",
                runs: "announce-pr",
            },
            {
                name: "pr-verdict",
                when: "When a pull_request row's verdict changes",
                runs: "announce-pr",
            },
            {
                name: "pull-requests",
                when: "When GitHub reports a pull request",
                runs: "pr-event",
            },
            {
                name: "review-queued",
                when: "When a pull_request row's review_state becomes queued",
                runs: "review-pr",
            },
            {
                name: "signal-new",
                when: "When a signal row is new",
                runs: "triager",
            },
            {
                name: "sync-open-prs",
                when: "Every 15 minutes",
                runs: "sync",
            },
            {
                name: "task-created",
                when: "When a task row is added or changed",
                runs: "announce-task",
            },
            {
                name: "task-pr-opened",
                when: "When a task row's pr changes",
                runs: "check-pr-public",
            },
            {
                name: "task-status",
                when: "When a task row's status changes",
                runs: "announce-task",
            },
            {
                name: "tell-waiting",
                when: "When a pull_request row's waiting_on changes",
                runs: "nudger",
            },
        ],
        connectors: [
            {
                service: "GitHub",
                mode: "SHARED",
                required: true,
                why: "Reads pull requests and posts the review check. Bound through a variable, not declared as a connector.",
                declared: false,
            },
            {
                service: "Slack",
                mode: "SHARED",
                required: false,
                why: "Posts pull request and issue news to a channel you pick.",
                declared: false,
            },
            {
                service: "PostHog",
                mode: "SHARED",
                required: false,
                why: "Lets issue triage ask how many people a bug reaches.",
                declared: false,
            },
        ],
        skills: [],
    },
    "project-management": {
        tables: [
            {
                name: "attachments",
                kind: null,
                eachPersonsOwn: false,
                columns: 10,
                key: ["issue_id", "file_path", "file_name", "source", "uploader_kind"],
            },
            {
                name: "comments",
                kind: null,
                eachPersonsOwn: false,
                columns: 8,
                key: ["id", "issue_id", "author_kind", "body", "is_ready"],
            },
            {
                name: "events",
                kind: null,
                eachPersonsOwn: false,
                columns: 9,
                key: ["id", "kind", "actor_kind"],
            },
            {
                name: "issues",
                kind: "task",
                eachPersonsOwn: false,
                columns: 16,
                key: ["id", "number", "title", "status", "priority", "assignee_kind"],
            },
            {
                name: "projects",
                kind: "project",
                eachPersonsOwn: false,
                columns: 8,
                key: ["id", "name", "lead_kind", "status"],
            },
        ],
        apps: [
            {
                name: "roundtable-hq",
                title: "Roundtable",
                shape: "board",
                line: "Projects and issues on one board, assigned to people or agents.",
                screens: [
                    "Board of issues",
                    "Projects",
                    "An issue or project page",
                    "Team: people and agents",
                    "Activity",
                    "Knowledge",
                    "Autopilots",
                ],
                inBundle: true,
                cover: false,
            },
        ],
        agents: [
            {
                name: "researcher",
                line: "Research specialist: competitive analysis, fact-finding, deep dives.",
            },
            {
                name: "squad-leader",
                line: "Routing agent: reads an issue and delegates to the best specialist (researcher, writer, or worker-default).",
            },
            {
                name: "worker-default",
                line: "General-purpose worker: picks up assigned issues, works them, reports in comments and moves them on.",
            },
            {
                name: "writer",
                line: "Content specialist: blog posts, docs, email drafts, changelogs.",
            },
        ],
        functions: [
            {
                name: "create_subissue",
                line: "Create a child issue linked to a parent.",
            },
            {
                name: "dispatch_issue",
                line: "Notify a human assignee once per assignment state, or create an agent conversation and fire the first turn.",
            },
            {
                name: "post_comment",
                line: "Post an agent comment and atomically hand the issue off as done, waiting, or still in progress.",
            },
            {
                name: "resume_issue",
                line: "Send a human reply into a waiting agent conversation.",
            },
            {
                name: "resume_issue_v2",
                line: "Resume a waiting issue from a ready human comment, including its native attachment manifest.",
            },
            {
                name: "save_deliverable",
                line: "Save an agent's finished deliverable as markdown under /knowledge/deliverables/RT-<n>-<slug>.md so it is searchable and shown on the task.",
            },
            {
                name: "transition_issue",
                line: "Update an issue's status/run_state and record the transition event.",
            },
        ],
        workflows: [
            {
                name: "dispatch-issue",
                line: "On issue insert/update, let dispatch_issue decide whether to start an agent conversation.",
                starts: "When a row changes",
            },
            {
                name: "resume-conversation",
                line: "When a human comments on a waiting issue, send the reply into its agent conversation.",
                starts: "When a row changes",
            },
        ],
        schedules: [
            {
                name: "dispatch-on-assign",
                when: "When a issues row is added or changed",
                runs: "dispatch-issue",
            },
            {
                name: "resume-on-comment",
                when: "When a comments row is added or changed",
                runs: "resume-conversation",
            },
            {
                name: "weekly-digest",
                when: "Mondays at 09:00",
                runs: "researcher",
            },
        ],
        connectors: [],
        skills: [],
    },
    "smart-inbox": {
        tables: [
            {
                name: "activity",
                kind: null,
                eachPersonsOwn: true,
                columns: 5,
                key: ["kind", "text", "thread_id", "ref_id"],
            },
            {
                name: "commitment",
                kind: "commitment",
                eachPersonsOwn: true,
                columns: 8,
                key: ["direction", "text", "quote", "due_at", "state"],
            },
            {
                name: "draft",
                kind: null,
                eachPersonsOwn: true,
                columns: 15,
                key: ["mailbox_id", "kind", "subject", "body", "state"],
            },
            {
                name: "mailbox",
                kind: null,
                eachPersonsOwn: true,
                columns: 10,
                key: ["provider", "account_id", "email", "state"],
            },
            {
                name: "meeting",
                kind: "meeting",
                eachPersonsOwn: true,
                columns: 15,
                key: ["provider", "provider_event_id", "title", "start_at", "status", "brief_state"],
            },
            {
                name: "memory",
                kind: null,
                eachPersonsOwn: true,
                columns: 6,
                key: ["about", "fact", "state"],
            },
            {
                name: "message",
                kind: "touch",
                eachPersonsOwn: true,
                columns: 15,
                key: [
                    "mailbox_id",
                    "provider_message_id",
                    "provider_thread_id",
                    "direction",
                    "subject",
                    "tier",
                ],
            },
            {
                name: "person",
                kind: "person",
                eachPersonsOwn: true,
                columns: 15,
                key: ["email", "name", "title"],
            },
            {
                name: "profile",
                kind: null,
                eachPersonsOwn: true,
                columns: 12,
                key: ["onboarding_step", "display_name", "timezone", "my_domains"],
            },
            {
                name: "rule",
                kind: null,
                eachPersonsOwn: true,
                columns: 6,
                key: ["scope", "text", "state"],
            },
            {
                name: "split",
                kind: null,
                eachPersonsOwn: true,
                columns: 10,
                key: ["key", "name", "rule", "kind", "state"],
            },
            {
                name: "sync_state",
                kind: null,
                eachPersonsOwn: true,
                columns: 11,
                key: ["mailbox_id", "kind", "state"],
            },
            {
                name: "thread",
                kind: "touch",
                eachPersonsOwn: true,
                columns: 34,
                key: ["mailbox_id", "provider", "provider_thread_id", "subject", "tier", "state"],
            },
        ],
        apps: [
            {
                name: "inbox",
                title: "Inbox",
                shape: "mail",
                line: "Gmail and Outlook in one list, sorted into splits you write in plain English, with replies drafted in your voice and briefs before your meetings.",
                screens: [
                    "Welcome and first read",
                    "Today",
                    "Inbox by split",
                    "Reader",
                    "Yours: drafts and promises",
                    "People",
                ],
                inBundle: true,
                cover: false,
            },
        ],
        agents: [
            {
                name: "assistant",
                line: "Answers questions about your mail, finds threads, writes and adjusts splits and rules, asks for drafts, snoozes and files threads.",
            },
            {
                name: "briefer",
                line: "Writes a one-page brief before a meeting with outside people: who they are, where you left off, open promises, what they will want, what you should get out of it.",
            },
            {
                name: "setup",
                line: "From the first read of someone's mail, learns how they write and proposes splits, VIPs and starter rules for them to accept.",
            },
            {
                name: "triage",
                line: "Sorts a batch of mail threads into your splits, decides what each needs from you, times follow-ups, and asks for drafts.",
            },
            {
                name: "writer",
                line: "Drafts one reply, nudge or scheduling email in the person's own voice from a prepared brief.",
            },
        ],
        functions: [
            {
                name: "apply_triage",
                line: "Validate and write the triage agent's decisions: split, state, follow-up time, drafts wanted, promises, memory and VIP suggestions.",
            },
            {
                name: "calendar_sync",
                line: "Read next week's meetings from every connected Google or Outlook calendar, mark the ones with outside people, and ask for a brief when one is within a day.",
            },
            {
                name: "ensure_profile",
                line: "Create the caller's profile and default splits if they are missing.",
            },
            {
                name: "mail_sync",
                line: "Read Gmail and Outlook into threads, messages and one searchable document per conversation.",
            },
            {
                name: "meeting_context",
                line: "Gather what your mail knows about the outside people on one meeting: who they are, recent threads, newest words, open promises.",
            },
            {
                name: "onboarding_report",
                line: "What the first read found: your own writing for the voice, top correspondents, conversation domains, bulk senders and totals.",
            },
            {
                name: "people_refresh",
                line: "Recompute who you deal with from your mail: counts, reciprocity, cadence and weight per person.",
            },
            {
                name: "resort_recent",
                line: "Send recent open threads back to triage after splits change, so new splits get the mail they should have had.",
            },
            {
                name: "save_brief",
                line: "Store a meeting brief and link the threads it drew on.",
            },
            {
                name: "save_draft",
                line: "Store the writer's draft (or its reason for not writing one) and mark the thread's draft ready.",
            },
            {
                name: "save_setup",
                line: "Store the setup agent's proposals: the voice file, and splits, VIPs and rules as proposals to accept.",
            },
            {
                name: "search_mail",
                line: "Search your own conversations (meaning and keywords) and return matching threads with excerpts.",
            },
            {
                name: "send_draft",
                line: "Deliver one approved draft from the mailbox its thread belongs to, in its thread.",
            },
            {
                name: "sweep",
                line: "The hourly pass: bring snoozed threads back, turn due follow-ups into ready nudges, and list approved drafts whose send time came.",
            },
            {
                name: "thread_action",
                line: "Do one thing to one or more threads (done, restore, read, unread, snooze, unsnooze, mute, state, split, draft) here and, where it applies, in Gmail or Outlook.",
            },
            {
                name: "triage_packets",
                line: "Build the triage agent's input for one batch: the newest words of each thread, what is known about each sender, your splits and your rules.",
            },
            {
                name: "triage_queue",
                line: "Claim the threads waiting for a decision and cut them into batches for the triage agent.",
            },
            {
                name: "writing_context",
                line: "Gather everything the writer needs for one draft: voice, rules, the thread, who to reply to, what is known about the other side, past lines to them, open promises, and free times when the ask is to meet.",
            },
        ],
        workflows: [
            {
                name: "catch-up",
                line: "Every 15 minutes: read anything new in every mailbox (and keep a first read going), then sort what arrived.",
                starts: "On a timer",
            },
            {
                name: "meeting-brief",
                line: "Write the brief for one upcoming meeting with outside people.",
                starts: "When a row changes",
            },
            {
                name: "nightly",
                line: "Nightly: recompute who you deal with from your mail.",
                starts: "On a timer",
            },
            {
                name: "on-gmail-received",
                line: "New Gmail: read it within a minute or two and sort it, instead of waiting for the catch-up.",
                starts: "When mail arrives or is sent",
            },
            {
                name: "on-gmail-sent",
                line: "Gmail you sent: read it within a minute or two and sort it, instead of waiting for the catch-up.",
                starts: "When mail arrives or is sent",
            },
            {
                name: "on-outlook-received",
                line: "New Outlook mail: read it within a minute or two and sort it, instead of waiting for the catch-up.",
                starts: "When mail arrives or is sent",
            },
            {
                name: "on-outlook-sent",
                line: "Outlook mail you sent: read it within a minute or two and sort it, instead of waiting for the catch-up.",
                starts: "When mail arrives or is sent",
            },
            {
                name: "setup",
                line: "Learn the person's voice and propose splits, VIPs and starter rules from the first read.",
                starts: "By hand",
            },
            {
                name: "sweep",
                line: "Hourly: bring back snoozed threads, ready nudges for follow-ups that came due, refresh next week's meetings, send drafts scheduled for now.",
                starts: "On a timer",
            },
            {
                name: "triage",
                line: "Sort the threads waiting for a decision: claim batches, judge each with the triage agent, write the results.",
                starts: "By hand",
            },
            {
                name: "write-draft",
                line: "Write the draft a thread asked for: gather the brief, let the writer draft it, store it beside the thread.",
                starts: "When a row changes",
            },
        ],
        schedules: [
            {
                name: "brief-wanted",
                when: "When a meeting row's brief_state becomes wanted",
                runs: "meeting-brief",
            },
            {
                name: "draft-wanted",
                when: "When a thread row's draft_state becomes wanted",
                runs: "write-draft",
            },
        ],
        connectors: [
            {
                service: "Gmail",
                mode: "PER_MEMBER",
                required: false,
                why: "Reads and sorts your mail; sends a reply only when you press Send.",
                declared: false,
            },
            {
                service: "Outlook",
                mode: "PER_MEMBER",
                required: false,
                why: "Same, for Outlook mail.",
                declared: false,
            },
            {
                service: "Google Calendar",
                mode: "PER_MEMBER",
                required: false,
                why: "Reads your meetings so each one with outside people gets a brief.",
                declared: false,
            },
        ],
        skills: [],
    },
    "booking-page": {
        tables: [
            {
                name: "bookings",
                kind: null,
                eachPersonsOwn: false,
                columns: 13,
                key: ["starts_at", "name", "email", "about", "status"],
            },
            {
                name: "open_slots",
                kind: null,
                eachPersonsOwn: false,
                columns: 2,
                key: ["starts_at", "ends_at"],
            },
            {
                name: "rules",
                kind: null,
                eachPersonsOwn: false,
                columns: 14,
                key: [
                    "timezone",
                    "weekdays",
                    "day_starts",
                    "day_ends",
                    "meeting_minutes",
                    "buffer_minutes",
                ],
            },
        ],
        apps: [
            {
                // The bundle names this app after its maker; that is one of
                // Booking page's gaps in catalog.ts, so it isn't repeated here.
                name: "booking",
                title: "Booking page",
                shape: "calendar",
                line: "Open times from the host's calendar, an email code, and the invite.",
                screens: [
                    "Pick a time",
                    "Confirm your email with a code",
                    "Booked, with the invite",
                ],
                inBundle: true,
                cover: false,
            },
        ],
        agents: [
            {
                name: "brief",
                line: "Looks up the person who booked a call and writes the host a short brief before it.",
            },
            {
                name: "host",
                line: "Answers questions beside the booking page: when the host is free, what a call is for, how booking works.",
            },
        ],
        functions: [
            {
                name: "confirm_booking",
                line: "Check a new booking against the rules and the calendar, then send the Google Calendar invite to the email the person confirmed, or decline it and tell them why.",
            },
            {
                name: "send_brief",
                line: "Keeps the brief on the booking and emails it to the host.",
            },
            {
                name: "sync_slots",
                line: "Rebuild open_slots from the rules, the calendar's busy times and the bookings already taken.",
            },
        ],
        workflows: [
            {
                name: "on-booking",
                line: "A new booking: send the invite or decline it, then brief the host on who's coming.",
                starts: "When a row changes",
            },
            {
                name: "refresh-slots",
                line: "Rebuild the open times from the calendar.",
                starts: "On a timer",
            },
        ],
        schedules: [
            {
                name: "on-booking",
                when: "When a bookings row is added or changed",
                runs: "on-booking",
            },
            {
                name: "refresh-slots",
                when: "Every 15 minutes",
                runs: "refresh-slots",
            },
        ],
        connectors: [
            {
                service: "Google Calendar",
                mode: "SHARED",
                required: true,
                why: "Reads the host's busy times and sends the invite.",
                declared: false,
            },
            {
                service: "Gmail",
                mode: "SHARED",
                required: false,
                why: "Sends the email code and the brief.",
                declared: false,
            },
            {
                service: "Outlook",
                mode: "SHARED",
                required: false,
                why: "Counts Outlook busy times too.",
                declared: false,
            },
        ],
        skills: [],
    },
};
