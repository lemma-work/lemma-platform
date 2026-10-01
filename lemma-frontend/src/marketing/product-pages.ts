/** The product pages, one per part of a teammate's space, each explaining
 *  what the part is, why it exists and everything it does, for somebody who
 *  has never seen Lemma.
 *
 *  Written by research, three competing drafts, a judge and a fact-check
 *  against the code, then edited for length; regenerate from those specs
 *  rather than editing by hand when the copy changes wholesale. Every
 *  picture is a real capture (scripts/capture-product-shots.mjs and the
 *  landing's own), and the moving ones are vignettes: small coded moments of
 *  the product in ../app/(marketing)/vignettes. A different sample teammate
 *  carries each page, so no one name becomes the product.
 *
 *  Groups is not here: it ships with the groups work (#869), and its page
 *  arrives with it. */

export type Who = "Kit" | "Remy" | "June" | "Scout";

export const FACES: Record<Who, string> = {
    Kit: "/teammates/loop-v1.png",
    Remy: "/teammates/pleat-v1.png",
    June: "/teammates/frame-v1.png",
    Scout: "/teammates/extended/gem.png",
};

export interface Still { kind: "still"; src: string; width: number; height: number; alt: string }
/** A recording of the real app: `/product/<name>.mp4`, `.webm` and a poster. */
export interface Loop { kind: "loop"; name: string; width: number; height: number; alt: string }
export type Picture = Still | Loop;

export type Visual =
    | { type: "none" }
    | { type: "picture"; picture: Picture; caption?: string; window?: string; who?: Who; maxWidth?: number }
    | { type: "vignette"; id: string; caption?: string }
    /** The four sample teammates' apps, each in its own window, full size. */
    | { type: "gallery"; caption?: string };

export interface Section {
    id: string;
    label: string;
    title: string;
    /** Paragraphs separated by a blank line. */
    body: string;
    points?: { title: string; body: string }[];
    visual: Visual;
    tone?: "sand" | "violet";
}

export interface ProductPage {
    slug: string;
    name: string;
    /** One line, for the nav menu. */
    navBlurb: string;
    metaDescription: string;
    face: Who;
    hero: { eyebrow: string; headline: string; intro: string; visual: Visual; who?: Who; shown?: string };
    sections: Section[];
    close: { headline: string; sub?: string };
}

const still = (src: string, width: number, height: number, alt: string): Still => ({ kind: "still", src, width, height, alt });
const loop = (name: string, alt: string): Loop => ({ kind: "loop", name, width: 1124, height: 860, alt });

export const PRODUCT_PAGES: ProductPage[] = [
    {
        slug: "pages",
        name: "Pages",
        navBlurb: "Documents you write with your teammate",
        metaDescription: "A page is a document you and your AI teammate write together: charts drawn from your tables, table views that read fresh rows, working widgets, and comments it acts on.",
        face: "Scout",
        hero: { eyebrow: "Pages", headline: "Documents you write with your teammate.", intro: "Type in a page like any doc. Ask your teammate and it writes, draws a chart or rewrites a passage right where you point. Everyone opens the same copy.", visual: { type: "picture", picture: still("/product/pages-hero.webp", 2248, 1720, "Scout's page Why trials stall, with a table view of five customer interviews inside it."), window: "Why trials stall", who: "Scout", caption: "Scout’s research memo, with a view of the interviews table." } },
        sections: [
            { id: "why", label: "Why pages", title: "Chat is for working it out. A page is where it stays.", body: "A good answer in chat scrolls away by tomorrow, and only the people in that thread saw it. A page keeps it where the whole team can open it, comment on it and keep it current: you by typing, your teammate when asked.", visual: { type: "vignette", id: "pages-thread-vs-page", caption: "The thread moves on. The page doesn’t." } },
            { id: "write", label: "Writing", title: "Ask from the spot you mean.", body: "There’s no edit mode: you type, and it saves. When you want help, ask from inside the page, and the words land where you were looking. Its edits never overwrite anything you haven’t saved.", points: [{ title: "At the cursor", body: "Type /, choose Ask to write, say what goes there." }, { title: "On a passage", body: "Select it, choose Ask for change: “shorter”, “warmer”, “make it a table”." }, { title: "In the page’s chat", body: "Talk it through beside the page, then say “put that in”." }], visual: { type: "vignette", id: "pages-ask-for-change", caption: "Only the selected passage changes." }, tone: "sand" },
            { id: "visualize", label: "Visualize", title: "Ask for a chart. It appears where you typed.", body: "Type /, choose Visualize and name it: interviews by theme. Your teammate finds the numbers in the page, a table or the web, counting only rows you can see, and draws the chart in place with the finding on top. It’s a snapshot; ask again to refresh it.", visual: { type: "vignette", id: "pages-visualize", caption: "Pulled from the interviews table, drawn in place." } },
            { id: "table-views", label: "Table views", title: "Put the table itself in the page.", body: "A pasted list is out of date by Thursday. A table view is a query, not a copy: pick a table, filter, group and sort, or write SQL. It reads the rows each time the page opens, as whoever is reading, so each person sees their part.", visual: { type: "vignette", id: "pages-live-view", caption: "Marked Ready in the table. The brief caught up." }, tone: "sand" },
            { id: "blocks", label: "Widgets", title: "Drop a small working tool into the page.", body: "A what-if on this month’s deals, a pricing calculator, a checklist that scores itself. Type / and choose HTML, or ask your teammate to build it. It runs sandboxed in the page, and you change it by asking: “add a total row”.", visual: { type: "vignette", id: "pages-widget-whatif", caption: "A what-if, built into the pipeline page." } },
            { id: "comments", label: "Comments", title: "Mention your teammate, and the comment gets done.", body: "Comments pin to the words they’re about and survive rewrites. Mention a person and they’re notified. Mention your teammate and it reads the page, makes the change and replies in the thread, with a Watch link while it works.", visual: { type: "vignette", id: "pages-comment-handoff", caption: "@Scout: edited, and replied in the thread." }, tone: "sand" },
            { id: "underneath", label: "Underneath", title: "Every page is a plain markdown file.", body: "Headings, bullets and to-dos are plain text; views and widgets are short fenced blocks. That’s why your teammate edits a page as easily as you do, and why a download opens anywhere.", points: [{ title: "Follows its title", body: "Rename a page and its file, sub-pages and attachments move with it." }, { title: "Pages inside pages", body: "Type /page to nest one. Images and files land where you typed." }, { title: "Clear about access", body: "Every page says who can open it, in one sentence." }, { title: "Share outside", body: "A read-only link that expires after some hours or opens." }, { title: "Searchable", body: "Search matches meaning, not just words, for you and your teammate." }, { title: "From Claude or ChatGPT", body: "Connect the space over MCP and edit its pages there, as you." }], visual: { type: "none" } },
        ],
        close: { headline: "Which plan is lost in a chat thread?", sub: "Start a page from the guide template. It explains pages by being one." },
    },
    {
        slug: "tables",
        name: "Tables",
        navBlurb: "Shared lists your teammate keeps current",
        metaDescription: "Tables are the shared lists your AI teammate and your people keep current: real Postgres rows, views that choose themselves, row-level access, and rows that start work.",
        face: "Remy",
        hero: { eyebrow: "Tables", headline: "The list your whole team keeps current.", intro: "Deals, follow-ups, customer files: one row each, in a real table. Your teammate updates rows as it works; your people see them as a board or a checklist and tick them off.", visual: { type: "picture", picture: still("/product/tables-hero.webp", 2248, 1720, "Remy's deals table shown as a board grouped by stage, each card with its owner and value."), window: "Deals", who: "Remy", caption: "Remy’s deals, piled by stage." } },
        sections: [
            { id: "what-a-table-is", label: "What a table is", title: "One row for each thing the work is about.", body: "Each deal, follow-up or customer file gets a row, and each fact a column. What your teammate learns lands in a row, where the next question, page or workflow finds it. You don’t design it: say what to track, and it sets up the columns.", visual: { type: "vignette", id: "tables-from-a-sentence", caption: "One sentence in, a table out." } },
            { id: "the-view-reads-the-rows", label: "Views that choose themselves", title: "Stages become a board. Due dates become a checklist.", body: "The table reads its own values and draws the view that fits, then says why. Show as table is always one click away.", points: [{ title: "Board", body: "Repeating stages, a name on every row." }, { title: "Checklist", body: "Due dates and done boxes, soonest first." }, { title: "Timeline", body: "Named things that already happened." }, { title: "Chart", body: "Only dates and numbers." }, { title: "Reading view", body: "Long writing a cell would cut off." }, { title: "Grid", body: "Everything else, and any very large table." }], visual: { type: "vignette", id: "tables-view-reads-rows", caption: "Due dates and done boxes: a checklist." }, tone: "sand" },
            { id: "kept-current", label: "Kept current", title: "Whoever hears the news moves the row.", body: "Saltbox signs. Priya tells your teammate in Slack, and it moves the deal to Won with Priya’s access and no more. Or she changes the stage herself in two clicks.", points: [{ title: "Move it", body: "Each card’s stage is a picker; a refused change bounces back with the reason." }, { title: "Tick it", body: "Done boxes are real. Ticked rows sink; late ones get flagged." }, { title: "Edit it", body: "Forms are built from the columns and checked before saving." }], visual: { type: "vignette", id: "tables-saltbox-signs", caption: "One message, and the deal moves to Won." } },
            { id: "each-person-their-rows", label: "Each person, their rows", title: "Everyone sees their own rows, and Postgres enforces it.", body: "Give rows an owner and row-level security does the rest: Priya and Aditi open the same Follow Ups and each sees theirs. When Priya asks in Slack, your teammate reads the table as Priya.", visual: { type: "vignette", id: "tables-own-rows", caption: "Same table, two people, two lists." }, tone: "sand" },
            { id: "ask-the-table", label: "Ask the table", title: "Ask a question, get rows back.", body: "Every table has its own chat. Ask which open deals are over 10,000 and your teammate runs one read-only query, then answers with the rows, the total and the query itself, so you can check it.", visual: { type: "vignette", id: "tables-ask-rows", caption: "45,500 across three deals, query shown." } },
            { id: "rows-start-work", label: "Rows start work", title: "A new row can start a workflow.", body: "A customer file added to imports starts the First import check on its own. Any table can trigger work: on every new row, or only when a deal’s stage changes to Won.", visual: { type: "picture", picture: still("/product/workflows-starts.webp", 2168, 584, "How it starts: a row added in imports, on for everyone; beside it the runs, one waiting on Dev at Dev approves the fix."), caption: "A row added in imports starts the check." }, tone: "sand" },
            { id: "underneath", label: "Underneath", title: "Real Postgres, shared by pages, apps and Claude.", body: "The same rows, under the same access rules, wherever they’re read.", points: [{ title: "Typed columns", body: "Text, numbers, dates, choices, people and links; required or unique." }, { title: "Read-only SQL", body: "Joins and totals, run as you, never past row security." }, { title: "Every row has a page", body: "With links both ways, kept for you." }, { title: "In pages and apps", body: "Pages show the same rows; apps read and write them." }, { title: "From Claude or ChatGPT", body: "Query and write rows over MCP, as you." }, { title: "From a spreadsheet", body: "Load a CSV with the Lemma CLI. No formulas." }], visual: { type: "picture", picture: still("/product/pages-view.webp", 1520, 622, "A table view inside a page listing five interviews by company, role, theme and excerpt."), caption: "The interviews table, inside a page." } },
        ],
        close: { headline: "What lives in a sheet nobody trusts?", sub: "Say what to track. Your teammate sets up the table and keeps it current." },
    },
    {
        slug: "workflows",
        name: "Workflows",
        navBlurb: "Work handed between steps and people",
        metaDescription: "Workflows run a job the same way every time: steps for your AI teammate, for code and for named people, started on a schedule or by a row, with every run on record.",
        face: "June",
        hero: { eyebrow: "Workflows", headline: "Work that passes through several hands.", intro: "A workflow runs a job the same way every time. Some steps go to your teammate, some run code, and some wait for one named person to decide.", visual: { type: "picture", picture: loop("workflows-loop", "Opening June's workflow and its waiting run, where Dev is asked to approve the corrected file."), caption: "June’s import check, waiting on Dev." } },
        sections: [
            { id: "why", label: "Why workflows", title: "The steps take minutes. The handoffs take days.", body: "A first import: check the rows, fix the dates, get a sign-off, run it. It drags for a week because the fixed file sits unseen and the approver doesn’t know it’s their turn. A workflow writes the handoffs down, so nobody has to remember.", visual: { type: "vignette", id: "workflows-handoff", caption: "One import, from new row to finished." } },
            { id: "why-not-chat", label: "When to make one", title: "Ask once in chat. By the fourth time, make it a workflow.", body: "The first time, work the job out together in chat. Once you’ve asked for the same thing three Mondays running, it has a shape: a fixed order, named people, the same result however it’s asked.", visual: { type: "picture", picture: still("/product/workflows-list.webp", 2080, 618, "The workflows list with tabs for Workflows, Waiting on you, Running and Recent runs."), caption: "Three jobs that keep coming back." }, tone: "sand" },
            { id: "steps", label: "Steps", title: "Seven kinds of step.", body: "Three do the work: your teammate reads and drafts, code does what must be exact, a person decides. Four steer the run. Each step hands what it found to the next.", points: [{ title: "Hand to your teammate", body: "A real conversation you can read or join." }, { title: "Ask a person", body: "One named person fills in a short form." }, { title: "Run code", body: "Exact work: validate, total, write, call another system." }, { title: "Branch", body: "The first rule that matches picks the path." }, { title: "Repeat", body: "The same steps for each item in a list." }, { title: "Wait", body: "Pause for a set time, then carry on." }, { title: "End", body: "Where a path finishes." }], visual: { type: "vignette", id: "workflows-steps", caption: "Seven kinds of step, one run’s path." } },
            { id: "starts", label: "How it starts", title: "It starts itself, with one person’s access.", body: "By hand, on a schedule, when a row changes, or when a connected app sends something. Every run acts as one person, so it never reaches further than they could.", points: [{ title: "Run now", body: "Runs as you." }, { title: "On a schedule", body: "Once or repeating, as often as every 15 minutes." }, { title: "When a row changes", body: "Added, changed or removed, optionally on a condition." }, { title: "When an app sends something", body: "A new email or message, through someone’s own account." }], visual: { type: "vignette", id: "workflows-starts", caption: "Four ways a run begins." }, tone: "sand" },
            { id: "your-turn", label: "Your turn", title: "It asks you once, in the chat you already use.", body: "When a step needs someone, your teammate messages them on Slack, Teams, WhatsApp, Telegram or email. Dev replies “Yes, import it. Tell Harbor the dates are fixed.” The form fills in from his words, and the run moves on.", points: [{ title: "Or in the app", body: "Waiting on you lists every pending step, oldest first." }, { title: "Only them", body: "Nobody else can answer it." }, { title: "Never stuck", body: "Unanswered after 72 hours, the run stops and says why." }], visual: { type: "vignette", id: "workflows-answer-in-chat", caption: "Dev replies in a sentence. The run moves on." } },
            { id: "record", label: "The record", title: "Every run leaves a page that explains itself.", body: "How far it got, what started it, where it’s waiting, and each step’s result and timing. A failed run names the step and the reason, so repeat failures stand out.", visual: { type: "picture", picture: still("/product/workflows-run.webp", 1744, 1252, "A run waiting on Dev to approve a corrected file, with each step's status and timing."), caption: "Three steps done, waiting on Dev." }, tone: "sand" },
            { id: "built-by-asking", label: "Making one", title: "Describe the job. Your teammate writes the steps.", body: "There’s no diagram to draw. Explain the job as you would to a new hire, read the steps it writes, then switch it on. To change it, ask again; the reasons stay in its chat.", points: [{ title: "Ask me first", body: "It prepares, a person approves, then it goes out." }, { title: "Only the unsure ones", body: "Clear cases pass; people see the edge cases." }, { title: "Failures become tasks", body: "A problem becomes a form for whoever can fix it." }], visual: { type: "vignette", id: "workflows-built-by-asking", caption: "One sentence in, the steps out." } },
        ],
        close: { headline: "Who doesn’t know it’s their turn?", sub: "Describe the job and who signs off. It runs itself from then on." },
    },
    {
        slug: "apps",
        name: "Apps",
        navBlurb: "Screens your teammate builds for one job",
        metaDescription: "Apps are screens your AI teammate builds for one recurring job, on your tables and under your permissions: review queues, import checkers, deal desks.",
        face: "June",
        hero: { eyebrow: "Apps", headline: "When chat isn’t the right shape, it builds an app.", intro: "An app is a small screen for one recurring job, like checking a customer’s file or reviewing a reply. Your teammate builds it on the data it keeps, and your people work in it as themselves.", visual: { type: "vignette", id: "apps-outgrew-chat", caption: "From an answer in chat to an app." } },
        sections: [
            { id: "why-a-screen", label: "Why apps", title: "Each person needs a different part of the answer.", body: "An answer in chat about Harbor’s file suits one person, once. Dev needs the bad cells and a button to fix them; Priya only needs to know it passed. An app gives each of them their part, and it’s still there next week.", points: [{ title: "Why not keep chatting?", body: "For a one-off, do. A job that keeps coming back deserves a screen." }, { title: "Why not buy or build?", body: "Bought tools impose their process. Building needs a database, sign-in and hosting; Lemma has those." }], visual: { type: "gallery", caption: "Four teammates, four apps, all different." } },
            { id: "prepares-you-decide", label: "Who does what", title: "Your teammate does the legwork. People decide.", body: "It drafts, flags and gathers in the background; the app is where a person checks and acts. Dev presses Normalize dates, and the unambiguous dates fix themselves while the bad email stays flagged for a person.", visual: { type: "vignette", id: "apps-june-normalize", caption: "Dates fixed. The email waits for a person." }, tone: "sand" },
            { id: "rules-built-in", label: "Your rules, built in", title: "The app knows rules a spreadsheet forgets.", body: "Edited copy needs approving again. Training waits for the first import. Tell your teammate the rules and it writes them into the app, so the screen remembers at five on a Friday.", visual: { type: "vignette", id: "apps-rules-built-in", caption: "Edit approved copy, and it needs review again." } },
            { id: "built-by-asking", label: "Making one", title: "Describe the job. Your teammate builds the app.", body: "Press Describe an app or pick an idea, and talk it through. Your teammate builds and tests it in its own sandbox, then hands it back with an Open button. To change it, ask in the app’s own chat.", points: [{ title: "App ideas", body: "Twenty starting points, shaped to how your team works." }, { title: "Small or full", body: "One HTML page, or a React app with several screens." }, { title: "Its own address", body: "Opens as a tab, or from a phone’s home screen." }], visual: { type: "picture", picture: still("/product/apps-catalog.webp", 2064, 928, "The Apps page with existing apps and a catalog of app ideas for sales."), caption: "Pick an idea; nothing’s built until you send it." }, tone: "sand" },
            { id: "as-themselves", label: "Permissions", title: "Everyone sees only what they’re allowed to.", body: "An app holds no keys of its own. Every request runs as whoever is signed in, under the same row rules as everywhere else, so Priya sees her accounts and you see yours, and nobody wrote a filter.", visual: { type: "vignette", id: "apps-two-viewers", caption: "One app, three people, three views." } },
            { id: "what-it-holds", label: "What goes in", title: "Built on the real rows, so it moves when they do.", body: "Apps read and write your teammate’s tables directly, and their lists can update live: a new follow-up slides into the open Deal desk without a reload.", points: [{ title: "Tables", body: "Read, write, join and total; lists update live." }, { title: "Files", body: "Shared folders, private files, converted documents." }, { title: "Your teammate", body: "A chat or a task inside the app." }, { title: "Workflow steps", body: "An inbox of steps waiting on whoever’s signed in." }, { title: "Connected tools", body: "Gmail or Slack actions on the person’s own account." }], visual: { type: "vignette", id: "apps-live-row", caption: "A new row slides in, no reload." }, tone: "sand" },
            { id: "widget-to-app", label: "The small version", title: "Asking for the same chart every Monday? Make it an app.", body: "For a quick question, your teammate draws a live widget right in the chat. When you keep asking for it, it becomes an app with its own tab, address and chat.", visual: { type: "vignette", id: "apps-widget-to-app", caption: "A widget becomes an app." } },
        ],
        close: { headline: "Which job do you still do by hand?", sub: "Describe it in a sentence. Your teammate builds the screen." },
    },
    {
        slug: "memory",
        name: "Memory",
        navBlurb: "Correct it once; it remembers",
        metaDescription: "Your AI teammate writes down what it learns in plain notes you can read and fix: shared notes for the team, personal notes only you can read.",
        face: "Remy",
        hero: { eyebrow: "Memory", headline: "Correct it once. It writes it down.", intro: "When someone corrects your teammate or settles how something is done, it writes a short note in the same reply, and checks its notes before it answers anyone. Every note is a file you can read and fix.", visual: { type: "picture", picture: still("/product/memory-noted.webp", 1608, 456, "Someone corrects Remy about who signs at Northstar; a line under the reply says Remy noted this."), caption: "Corrected once about who signs." } },
        sections: [
            { id: "why", label: "Why notes", title: "Lessons shouldn’t vanish when the chat ends.", body: "An AI that forgets makes you its memory: you and every colleague repeat the same corrections. So your teammate lifts each lesson out of the chat and into its space, where it holds for whoever asks next.", visual: { type: "vignette", id: "memory-chat-ends", caption: "The chat ends. The note stays." } },
            { id: "what-goes-in", label: "What it keeps", title: "What you’d otherwise tell a new hire.", body: "Corrections, decisions with their reasons, how the team works, how you like things. Not small talk, and not figures that belong in a table. When a fact changes, it rewrites the note rather than contradict it.", points: [{ title: "A correction", body: "“Anita signs at Northstar, not Raj.”" }, { title: "A team rule", body: "“Only the approved security overview goes out.”" }, { title: "A preference", body: "“Keep drafts under five lines.”" }], visual: { type: "picture", picture: still("/product/memory-shared.webp", 1568, 640, "What Remy remembers, shared: notes on Northstar, security documents and follow-ups."), caption: "A dot means it changed this week." }, tone: "sand" },
            { id: "shared", label: "Shared notes", title: "Tell it on Monday. Priya gets the answer on Wednesday.", body: "What the team should know goes in shared notes that apply to every conversation, in the app, in Slack or by email. A note written at 10:02 is in use at 10:05. Nothing is retrained.", visual: { type: "vignette", id: "memory-next-person", caption: "One note links two conversations." } },
            { id: "personal", label: "Personal notes", title: "Things about you stay yours.", body: "Your preferences go in notes only you can read, and they follow you into Slack and WhatsApp. A colleague’s conversation never loads them. Say “just for me” or “for the team” when it matters.", visual: { type: "picture", picture: still("/product/memory-personal.webp", 1568, 492, "What Remy remembers, personal: your preferences, which only you can read."), caption: "Only you can read these." }, tone: "sand" },
            { id: "before-it-answers", label: "Before it answers", title: "It checks its notes before every reply.", body: "Each set of notes has a short index. Your teammate reads the indexes first and opens a note when a line matters, most personal first, so your own preferences aren’t crowded out.", points: [{ title: "You, with this teammate", body: "How you like it to work with you." }, { title: "You", body: "Facts about you, like your role." }, { title: "Its own work", body: "Its working notes on the jobs it runs." }, { title: "Everyone", body: "Facts about the team, like who signs." }], visual: { type: "vignette", id: "memory-checks-notes", caption: "Four indexes, narrowest first." } },
            { id: "open-and-fix", label: "Read and fix", title: "Every note is a file you can open.", body: "Notes are plain markdown, not a hidden store. Click the noted line under a reply to open what it wrote. Change Friday to Thursday, and its next answer says Thursday.", visual: { type: "vignette", id: "memory-fix", caption: "Change the file, change the answer." }, tone: "sand" },
            { id: "skills", label: "Skills", title: "For a whole procedure, write a skill.", body: "Notes are a line or two. A procedure, like answering a security questionnaire, becomes a skill: a written how-to it follows whenever a task matches. Start one with New skill on its About page.", visual: { type: "vignette", id: "memory-note-and-skill", caption: "A note is a line. A skill is the procedure." } },
        ],
        close: { headline: "What have you explained three times?", sub: "Say it once more. It writes it down for everyone." },
    },
    {
        slug: "channels",
        name: "Channels",
        navBlurb: "Ask from Slack, WhatsApp or email",
        metaDescription: "Your AI teammate answers in Slack, Microsoft Teams, WhatsApp, Telegram and email, with each person’s own access and the same memory everywhere.",
        face: "Kit",
        hero: { eyebrow: "Channels", headline: "Ask from Slack, WhatsApp or email.", intro: "Your teammate takes requests wherever your team already talks. It knows who’s asking, works with their access, and puts the work in its space for everyone to see.", visual: { type: "vignette", id: "channels-three-apps", caption: "Three apps, one launch list." } },
        sections: [
            { id: "what-a-channel-is", label: "What a channel is", title: "A front door to your teammate’s space.", body: "People ask from where they already are, and your teammate comes to them when a step needs their answer. It’s the same teammate with the same memory on every channel, and the work always lands in its space.", points: [{ title: "Slack", body: "In the channels you allow, under its own name." }, { title: "Microsoft Teams", body: "Like Slack, set up with your Microsoft account." }, { title: "WhatsApp", body: "One to one, matched by verified mobile." }, { title: "Telegram", body: "Lemma’s bot, or one under its own name." }, { title: "Email", body: "Its own address from the day it’s hired." }], visual: { type: "picture", picture: still("/product/channels-reach.webp", 1040, 1460, "Where to reach a teammate: email connected; Telegram, Slack, Microsoft Teams and WhatsApp ready to connect."), caption: "Email works on day one. The rest take a minute." } },
            { id: "who-is-asking", label: "Who’s asking", title: "It always knows who’s asking.", body: "Every message is matched to a person: Slack and Teams by work email, WhatsApp by verified mobile, email by a sender the mail server vouches for. It acts with that person’s access, so Sam can’t mark the press release Ready just because your teammate could.", points: [{ title: "Newcomers", body: "Sign up right in the chat with an email code." }, { title: "Outsiders", body: "Told they need access, and nothing more." }], visual: { type: "vignette", id: "channels-who-is-asking", caption: "Same request, two people, two outcomes." }, tone: "sand" },
            { id: "in-the-thread", label: "In the thread", title: "In a busy channel, it waits to be mentioned.", body: "It answers only in channels you allow, and only when mentioned or in its own threads. It reads the thread above, and brings back previews, a table’s first rows or a document’s first page, with a link to the real thing.", visual: { type: "vignette", id: "channels-slack-working", caption: "A preview in Slack. The table stays in Lemma." } },
            { id: "approvals", label: "Approvals", title: "Only the person who asked can say yes.", body: "Risky actions, like emailing the press list, wait for Approve and Deny buttons in the chat, or a reply by email. A colleague’s tap is refused, and an approval never reaches past the asker’s own access.", visual: { type: "vignette", id: "channels-approval", caption: "Rohan’s tap is refused. Priya decides." }, tone: "sand" },
            { id: "hand-offs", label: "Hand-offs", title: "A workflow step finds Dev on his phone.", body: "When a step waits on Dev, your teammate messages him where he last talked to it. He replies “Yes, import it. Tell Harbor the dates are fixed.” The form fills in from his words, and the run moves on.", visual: { type: "vignette", id: "channels-step-finds-person", caption: "Answered on WhatsApp. The import runs." } },
            { id: "asking-around", label: "Asking around", title: "It asks three people and brings back one answer.", body: "Need to know whether the demo is cut and finance has signed off? It messages each person on their own app, waits without clogging your thread, and comes back once the last reply is in.", visual: { type: "vignette", id: "channels-round-up", caption: "Three people, three apps, one answer." }, tone: "sand" },
            { id: "your-ai-tools", label: "Claude and ChatGPT", title: "Bring the team’s data into Claude or ChatGPT.", body: "Connect your teammate’s tables and files to any MCP client with one link. It works as you, sees only what you can, and every call is logged.", visual: { type: "none" } },
        ],
        close: { headline: "Your teammate already has an email address.", sub: "Forward it the request sitting in your inbox. Add Slack or WhatsApp later." },
    },
];

export function productPage(slug: string): ProductPage | undefined {
    return PRODUCT_PAGES.find((page) => page.slug === slug);
}
