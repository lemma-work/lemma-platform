/** Pages to start from.
 *
 *  Plain markdown, like every page: a template is only a first draft, and the
 *  page made from it is an ordinary file the space's bots read and edit. The
 *  guide is the one that matters — it explains pages by being one, so every
 *  thing it describes is something you can try on the line below it.
 */

export interface PageTemplate {
    id: "guide" | "todo" | "tracker" | "weekly";
    title: string;
    blurb: string;
    /** The file name it is saved under, before any " 2" that keeps it unique. */
    file: string;
    body: (bot: string, today: Date) => string;
}

const DEMO_WIDGET = `<!doctype html>
<html><body style="margin:0;font:14px system-ui,sans-serif;color:#1f1f1f">
<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px">
  <b>Ideas by stage</b>
  <button id="go" style="border:0;border-radius:999px;padding:6px 12px;background:#1f1f1f;color:#fff;cursor:pointer">Shuffle</button>
</div>
<div id="bars" style="display:flex;gap:10px;align-items:flex-end;height:120px"></div>
<div id="labels" style="display:flex;gap:10px;margin-top:6px;font-size:12px;color:#777"></div>
<script>
const stages=["Idea","Draft","Review","Shipped"];
const draw=()=>{const bars=document.getElementById("bars"),labels=document.getElementById("labels");
bars.innerHTML="";labels.innerHTML="";
for(const s of stages){const v=2+Math.round(Math.random()*8);
const b=document.createElement("div");b.style.cssText="flex:1;border-radius:8px 8px 0 0;background:#6b4fe0;transition:height .3s;height:"+(v*11)+"px";b.title=s+": "+v;bars.appendChild(b);
const l=document.createElement("div");l.style.flex="1";l.textContent=s+" · "+v;labels.appendChild(l);}};
document.getElementById("go").onclick=draw;draw();
</script>
</body></html>`;

function week(today: Date): string {
    const monday = new Date(today);
    monday.setDate(today.getDate() - ((today.getDay() + 6) % 7));
    return monday.toLocaleDateString(undefined, { day: "numeric", month: "long" });
}

export const PAGE_TEMPLATES: PageTemplate[] = [
    {
        id: "guide",
        title: "Your guide to pages",
        blurb: "How pages work, by trying them",
        file: "Your guide to pages",
        body: (bot) => `# Your guide to pages

> 💡 A page is a doc you write in, with ${bot} one keystroke away. Use this one as a playground — change anything, nothing breaks. Tick things off as you try them.

## Getting started

- [ ] **Add a block.** Type \`/\` on an empty line for headings, to-dos, tables, images, files and more. Try it on a new line under this list 👇
- [ ] **Move things around.** Point at a block and drag the ⋮⋮ handle beside it; the + adds a block underneath.
- [ ] **Nest pages.** Type \`/page\` to make a page inside this one. It opens straight away, and shows up here as a card.


## Working with ${bot}

- [ ] **Ask it to write.** Type \`/\`, choose *Ask ${bot} to write*, and say what you want. It writes it in place, where you asked.
- [ ] **Ask for a change.** Select some text and choose *Ask for change* — "make this shorter", "turn this into a table".
- [ ] **Talk it through.** *Ask*, in the bottom corner, opens a chat about this page. Its expand button makes it full size, with the page beside it.

## Comments

- [ ] **Comment on anything.** Select text and choose *Comment*. Threads sit beside the page; resolve them when they are done.
- [ ] **Ask ${bot} in a comment.** Mention it — \`@${bot} tighten this\` — and it reads the page, makes the change and answers in the thread. The first time, choose *Let ${bot} answer* under the comment box.
- [ ] **Bring people in.** Mention someone with \`@\` and they get a notification.

## Live data and visuals

- [ ] **Show a table, live.** \`/table view\` picks one of your tables and lets you filter, sort and group it — or write SQL, joins included. It stays current; nothing is copied into the page.
- [ ] **Draw it.** \`/visualize\` asks ${bot} for a chart or a diagram, built from this page or your tables. \`/html\` embeds your own.

This one is drawn live, right here in the page — press Shuffle:

\`\`\`lemma-widget
${DEMO_WIDGET}
\`\`\`

## Sharing

- [ ] **Share**, top right, shows who can open this page and invites more people. Everything here is a plain file in ${bot}’s space, so ${bot} can read and edit it too.
`,
    },
    {
        id: "todo",
        title: "To-do list",
        blurb: "Today, this week, later",
        file: "To-do list",
        body: () => `# To-do list

## Today

- [ ] The one thing that has to happen
- [ ] Something small to get going

## This week

- [ ] A bigger piece of work

## Later

- [ ] Worth doing, not now
`,
    },
    {
        id: "tracker",
        title: "Project tracker",
        blurb: "Goal, milestones, decisions",
        file: "Project tracker",
        body: (bot) => `# Project tracker

> **Goal:** what done looks like, in one sentence. **Owner:** who. **Due:** when.

## Status

On track. Next milestone: the first draft.

## Milestones

| Milestone | Owner | Due | Status |
| --- | --- | --- | --- |
| Kick-off |  |  | Done |
| First draft |  |  | In progress |
| Review |  |  | Not started |
| Launch |  |  | Not started |

## Open questions

- [ ] What still has to be decided?

## Decisions

- What was decided, and why.

## Notes

If the work lives in a table, type \`/table view\` to show it here live rather than copying rows in — or ask ${bot} to keep this page up to date.
`,
    },
    {
        id: "weekly",
        title: "Weekly update",
        blurb: "Highlights, numbers, blockers",
        file: "Weekly update",
        body: (bot, today) => `# Weekly update — week of ${week(today)}

## Highlights

- What moved this week.

## Numbers

Type \`/visualize\` and ask ${bot} for this week's numbers as a chart, or \`/table view\` for the rows behind them.

## Blockers

- [ ] What is stuck, and who can unstick it.

## Next week

- [ ] What comes next.
`,
    },
];

/** A name not already taken in the folder: "Weekly update", then "Weekly update 2". */
export function freeName(base: string, taken: Set<string>): string {
    const has = (name: string) => taken.has((name + ".md").toLowerCase());
    if (!has(base)) return base;
    for (let n = 2; n < 500; n++) if (!has(base + " " + n)) return base + " " + n;
    return base + " " + Date.now().toString(36);
}

/** Make a page under a free name, never over an existing one.
 *
 *  `taken` is only a hint — it can be stale, or a listing may have failed —
 *  so the real guard is `create`, which must refuse an existing path with a
 *  409. On a refusal the next name is tried. Returns the path it made. */
export async function makePage(
    create: (path: string, text: string) => Promise<void>,
    base: string,
    text: string,
    taken: Set<string>,
): Promise<string> {
    const seen = new Set(taken);
    for (let attempt = 0; attempt < 25; attempt++) {
        const name = freeName(base, seen);
        const path = "/pages/" + name + ".md";
        try {
            await create(path, text);
            return path;
        } catch (error) {
            if ((error as { statusCode?: number } | null)?.statusCode !== 409) throw error;
            seen.add((name + ".md").toLowerCase());
        }
    }
    throw new Error("Couldn’t find a free name for the page. Try again.");
}
