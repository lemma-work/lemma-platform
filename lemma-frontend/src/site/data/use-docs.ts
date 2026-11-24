import type { DocsGroup, DocsPage } from "@/site/data/docs";
import { githubUrl } from "@/site/links";

/* The half of the docs for people using Lemma, and the one page on where it
   runs. Written for somebody who will never open a terminal: the words are
   the app's own (a Lemma, Chats, Pages, Tables, Files, Apps, Automations,
   People, Connectors), and every claim is one a scenario in
   `docs/product/journeys` makes. */

const USE = "Use Lemma";
const SET_UP = "Set up";

export const useLemmaPages: DocsPage[] = [
    {
        slug: "use/lemmas",
        title: "Lemmas",
        eyebrow: USE,
        group: USE,
        description:
            "A Lemma takes on one kind of work. You talk to it, it does the work, and it keeps what that work needs: chats, apps, tables, pages and files.",
        blocks: [
            {
                type: "paragraph",
                title: "What a Lemma is",
                body: "Make one Lemma for each kind of work: follow-ups, quotes for customers, client intake. Each has a name and a face, and you can ask it something as soon as it exists. By default, everyone in a Lemma can open what it keeps.",
            },
            {
                type: "steps",
                title: "Make a Lemma",
                items: [
                    "In the sidebar, choose New Lemma, the + beside Lemmas.",
                    "Give it a name. The name is the only thing you have to give, and no other Lemma in your organization can already have it.",
                    "If you want, say what it does in a sentence, and choose who can find it: “Only people I add”, or everyone at your organization.",
                    "Choose “Make it”. The Lemma asks who should work with it. Add people now, or later.",
                ],
            },
            {
                type: "paragraph",
                body: "You are its admin, and it opens on its Home.",
            },
            {
                type: "table",
                title: "What is inside",
                body: "Every Lemma has the same seven tabs.",
                columns: ["Tab", "What it holds"],
                rows: [
                    [
                        "Home",
                        "Its apps, then what is waiting on you, then what it has been doing.",
                    ],
                    ["Chats", "Your conversations with it."],
                    ["Apps", "The screens it builds for the work."],
                    [
                        "Tables",
                        "What you keep track of, one row each. It sets them up and fills them in.",
                    ],
                    [
                        "Pages",
                        "Documents everyone in it can open. It reads them and writes its own.",
                    ],
                    [
                        "Files",
                        "PDFs, documents, sheets and images you upload for it to read. What it makes lands here too.",
                    ],
                    [
                        "About",
                        "What it is for, who is in it, who can talk to it, what it does by itself, what it has been taught and what it is connected to.",
                    ],
                ],
            },
            {
                type: "list",
                title: "Start from an empty Lemma",
                body: "The Home of a new Lemma offers four ways in.",
                items: [
                    "Ask it. Say what you need in the chat box.",
                    "Bring things in from where the work is today.",
                    "Add a template. Its tables, skills and automations come in, and the Lemma starts a chat to set them up.",
                    "Make a page, a table or an app, or upload files.",
                ],
            },
            {
                type: "paragraph",
                body: "When you pick a suggestion, the request goes into the chat box for you to change or send. Nothing is sent for you.",
            },
            {
                type: "list",
                title: "What to expect",
                items: [
                    "It works with your access. It cannot open or change anything you cannot.",
                    "It asks before it deletes records, removes a table or sends a message outside the Lemma. If nobody answers within 7 days, it takes that as a no.",
                    "It can stop to ask you a question, and carries on when you answer.",
                    "You can stop it at any point. It keeps what it had made so far.",
                    "Tell it how you do something and it writes that down. Notes everyone shares are listed under About. Notes it keeps for you alone are not.",
                ],
            },
            {
                type: "paragraph",
                title: "Delete a Lemma",
                body: "An admin of the Lemma, or an owner of the organization, can delete it from its Settings. Its chats, pages, tables, files and apps can no longer be opened, its automations stop, and it stops answering everywhere it was connected. Its name becomes free to use again.",
            },
            {
                type: "links",
                title: "Next",
                items: [
                    {
                        label: "Apps",
                        href: "/docs/use/apps",
                        note: "Give the work a screen.",
                    },
                    {
                        label: "Automations",
                        href: "/docs/use/automations",
                        note: "Have it do work without being asked.",
                    },
                    {
                        label: "People",
                        href: "/docs/use/people",
                        note: "Decide who is in it.",
                    },
                ],
            },
        ],
    },
    {
        slug: "use/apps",
        title: "Apps",
        eyebrow: USE,
        group: USE,
        description:
            "An app is a screen a Lemma builds for its work: a queue to clear, a board to move, a form to fill in. You ask the Lemma for one, and the people in it work there.",
        blocks: [
            {
                type: "paragraph",
                title: "What an app is",
                body: "Some work needs a screen and not a chat. An app belongs to one Lemma and works on what that Lemma keeps, so you do not have to know how it is set up. It acts as you: an app can do only what you are allowed to do.",
            },
            {
                type: "steps",
                title: "Ask for an app",
                items: [
                    "Open the Lemma’s Apps tab. It is there even when the Lemma has no apps yet.",
                    "Choose “Describe an app” and say what you need, or pick one of the ideas shown.",
                    "The request goes into the chat box. Change it if you want, then send it.",
                    "The Lemma builds the app. It appears under Apps when it is ready, without a reload.",
                ],
            },
            {
                type: "list",
                title: "Open and use an app",
                items: [
                    "A Lemma that has apps opens on its first app. One with no apps opens on its Home.",
                    "The sidebar lists an open Lemma’s apps under its name.",
                    "Apps, in the sidebar, lists every app you can open, from all your Lemmas.",
                    "The address names the Lemma, the app and the page you are on inside it. Copy it to send someone to the same record. Reload, Back and Forward work as they do on any site.",
                    "An app follows your light or dark appearance.",
                    "One app can open another app of the same Lemma, for example to jump from a deal to the customer behind it.",
                ],
            },
            {
                type: "list",
                title: "Who can open an app",
                body: "An app starts open to everyone in the Lemma. It can be narrowed to only you, or to the people it is shared with.",
                items: [
                    "Someone who is not in the Lemma cannot open its apps.",
                    "Someone who is signed out is asked to sign in, and then lands on the page they were going to.",
                    "Opening one app gives no access to any other.",
                    "When someone loses access to an app, it stops loading for them within a minute, even where they had it open.",
                    "Two people in the same app can see different rows. Each sees what their own access allows.",
                ],
            },
            {
                type: "paragraph",
                title: "Change an app",
                body: "Ask the Lemma in a chat, the same way you asked for the app. Say what should be different.",
            },
            {
                type: "links",
                title: "Related",
                items: [
                    {
                        label: "Where to reach a Lemma",
                        href: "/docs/use/reach",
                        note: "Open a Lemma’s apps from Claude or ChatGPT.",
                    },
                    {
                        label: "Build an App",
                        href: "/docs/guides/build-a-app",
                        note: "For developers: write or change an app’s code yourself.",
                    },
                ],
            },
        ],
    },
    {
        slug: "use/automations",
        title: "Automations",
        eyebrow: USE,
        group: USE,
        description:
            "An automation is work a Lemma does without being asked: on a time, when something changes, or step by step in a fixed order. You describe it in words and the Lemma sets it up.",
        blocks: [
            {
                type: "paragraph",
                title: "What an automation is",
                body: "A Lemma’s Automations page lists everything it does on its own. There are two kinds. A schedule starts work at a time or when something happens. A workflow runs the same steps in the same order every time. To get there, open the Lemma’s About tab and choose “Everything it runs”.",
            },
            {
                type: "steps",
                title: "Set one up",
                items: [
                    "On the Automations page, say what to do and when. For example: Every Monday at 9, write the weekly report.",
                    "Choose “Set it up”. This starts a chat with the Lemma.",
                    "Answer what it asks. It makes the automation, and only then does it show in the list.",
                ],
            },
            {
                type: "table",
                title: "What can start it",
                columns: ["Start", "How it works"],
                rows: [
                    [
                        "A time",
                        "Once, or on a repeat, in the time zone you choose. It keeps to that time when the clocks change.",
                    ],
                    [
                        "A change in a table",
                        "A row is added, changed or removed. The row is handed to the work.",
                    ],
                    [
                        "An event from outside",
                        "Something a connected app reports. Each connector lists the events it offers.",
                    ],
                    [
                        "You",
                        "Start a workflow by hand, or choose “Run now” on a timed schedule.",
                    ],
                ],
            },
            {
                type: "paragraph",
                body: "An automation that starts on a change or an event can carry a condition, such as only quotes over a set amount. When the condition is not met, the run is recorded as skipped, not failed.",
            },
            {
                type: "paragraph",
                title: "Workflows and the people in them",
                body: "A step in a workflow can be work for the Lemma, a choice between branches, a repeat over a list, a wait, or a question for a named person. A run that reaches a question waits for that person’s answer, then carries on from where it stopped. Only the person asked can answer. “Waiting on you”, under the list, shows the steps waiting for you.",
            },
            {
                type: "list",
                title: "Watch and control",
                items: [
                    "Each row says how it is doing: Ran, Skipped, Failed, Paused or Waiting on a person.",
                    "Open a row to see its recent runs. “All runs” lists every one.",
                    "“Pause it” stops an automation and keeps it and its history. “Turn it on” starts it again from that point, without making up the runs it missed.",
                    "You can stop a run in progress. A workflow run that failed can be tried again from the step that failed, without repeating the steps before it.",
                    "What a scheduled run says lands in Chats.",
                ],
            },
            {
                type: "list",
                title: "What to expect",
                items: [
                    "The work is done once for each start, even when the same event arrives twice.",
                    "When a start fails for a reason that might pass, Lemma tries it again.",
                    "An automation that keeps failing is turned off, and the people responsible for the Lemma are told why. It is never turned off silently.",
                    "A run a person starts works with that person’s access, and no more.",
                    "An automation still asks a person before it deletes anything, unless that exact action was allowed in advance.",
                    "Deleting a Lemma stops all its automations.",
                ],
            },
            {
                type: "links",
                title: "Related",
                items: [
                    {
                        label: "Connectors",
                        href: "/docs/use/connectors",
                        note: "The apps an automation can work in.",
                    },
                    {
                        label: "Workflows",
                        href: "/docs/cli/workflows",
                        note: "For developers: write a workflow as a file.",
                    },
                ],
            },
        ],
    },
    {
        slug: "use/reach",
        title: "Where to reach a Lemma",
        eyebrow: USE,
        group: USE,
        description:
            "Nobody has to open Lemma to talk to a Lemma. It answers by email from the start, and you can turn on Slack, Microsoft Teams, WhatsApp and Telegram, or use it from Claude and ChatGPT.",
        blocks: [
            {
                type: "paragraph",
                title: "Email works from the start",
                body: "Every Lemma has an email address of its own that nobody had to set up. Write to it from the address you signed in with. It replies in the same thread, with everything in one message. When it has a question for you, the question is in that reply, and you answer by replying.",
            },
            {
                type: "steps",
                title: "Turn on another place",
                items: [
                    "Open the Lemma, choose More at the top right, then “Where to reach it”.",
                    "Each place says what it takes: One tap, About a minute, or Needs your account.",
                    "Choose Turn on, Set up or Connect, and follow the steps. Lemma says what is still needed at each one.",
                    "When it is done you see the address or name to write to. Copy it and send it to the people who need it.",
                ],
            },
            {
                type: "paragraph",
                body: "On a platform with channels or groups, the Lemma answers only in the ones you name. Bringing it into a group names that group.",
            },
            {
                type: "list",
                title: "Talking to it there",
                items: [
                    "The answer comes back in the same channel and thread you asked in.",
                    "Questions and approvals work everywhere: as buttons where the platform has them, as plain text where it does not. Press the button or type your answer.",
                    "Attach a file and the Lemma gets it. A voice message is turned into text.",
                    "Writing again on the same platform carries on your conversation there. Another platform, or Lemma itself, is a separate conversation.",
                    "In a group, each person has their own conversation with it.",
                    "Every conversation can be read in Lemma, marked with where it came from.",
                ],
            },
            {
                type: "list",
                title: "Who it answers",
                items: [
                    "The Lemma works out who you are in Lemma, and you get the access your account has. No more.",
                    "Being in the same Slack channel as a Lemma does not put you in the Lemma.",
                    "Someone with no access gets nothing from the Lemma, and is told how to ask for access.",
                    "On Telegram it knows you once you share your own contact, and the number matches the one verified on your Lemma account. A username is not enough.",
                ],
            },
            {
                type: "steps",
                title: "Use it from Claude or ChatGPT",
                items: [
                    "Open the Lemma’s Settings and choose MCP. It gives you one link for that Lemma.",
                    "Add the link in Claude, ChatGPT or another AI app that speaks MCP. There is nothing to install.",
                    "Sign in when asked. Lemma shows which tool is asking and which Lemma it wants. You can allow it to read only.",
                ],
            },
            {
                type: "paragraph",
                body: "The tool can read and change only what you can, in that one Lemma. Unless you allowed reading only, it can also open the Lemma’s apps. Settings lists the tools you connected and when each was last used. Disconnect one and its next request is refused.",
            },
            {
                type: "paragraph",
                title: "Know when it needs you",
                body: "Inbox, the bell at the top of a Lemma, holds what needs you there and shows how much you have not read. Where it asks you something, you can answer in the inbox. A question stays until you answer it. “Needs you”, in the sidebar, gathers what is waiting across all your Lemmas.",
            },
            {
                type: "paragraph",
                title: "Disconnect a place",
                body: "Under “Where to reach it”, choose Manage, then Disconnect. People who write there stop reaching the Lemma.",
            },
        ],
    },
    {
        slug: "use/people",
        title: "People",
        eyebrow: USE,
        group: USE,
        description:
            "The people in a Lemma share it: they open what it keeps and ask it for work. You decide who is in, what each of them can do, and whether others can find it.",
        blocks: [
            {
                type: "steps",
                title: "Add people",
                items: [
                    "Open the Lemma, choose More at the top right, then “Who has access”.",
                    "Type a name or an email address, and pick an access level.",
                    "Choose Add for someone already in your organization. They are in straight away.",
                    "Choose Invite for someone who is not. They get an invitation, and accepting it puts them in the organization and in this Lemma.",
                ],
            },
            {
                type: "paragraph",
                title: "Access levels",
                body: "There are four, from least to most: Can read, Can use, Can edit and Admin. An admin can also add and remove people. You cannot give anyone more than you have yourself. A change takes effect on that person’s next action.",
            },
            {
                type: "table",
                title: "Who can find it",
                body: "A new Lemma is found only by the people in it, unless you choose otherwise.",
                columns: ["Setting", "What happens"],
                rows: [
                    [
                        "Only people I add",
                        "Nobody can walk in. Others ask for access, and an admin of the Lemma or an owner of the organization decides and picks their access level.",
                    ],
                    [
                        "Everyone at your organization",
                        "Anyone in the organization can find it and join, with Can use.",
                    ],
                    [
                        "Anyone with a Lemma account",
                        "Any signed-in person can join. They are added to the organization as a member on the way in.",
                    ],
                ],
            },
            {
                type: "paragraph",
                body: "A Lemma open to the organization can also take in everyone who joins the organization from then on. An owner of the organization can open every Lemma in it.",
            },
            {
                type: "list",
                title: "Change access or remove someone",
                items: [
                    "Change a person’s access level in the same list.",
                    "Remove takes away their access to the Lemma and everything in it, from their next action. Work running in their name stops too.",
                    "If they are added back later, anything that was shared with them one by one is not restored.",
                    "A Lemma always has at least one admin. Lemma refuses to remove or change the last one until another is named.",
                ],
            },
            {
                type: "paragraph",
                title: "Share one thing with fewer people",
                body: "A table, a file or an app starts open to everyone in the Lemma. You can narrow it to only you or to named people. Narrowing takes effect at once. If the change would stop an automation that depends on it, Lemma says so first.",
            },
            {
                type: "list",
                title: "Contacts: people outside who write in",
                body: "A customer on WhatsApp or a client writing by email does not have to be in the Lemma. They are its contacts.",
                items: [
                    "A Lemma starts by answering only the people in it. Where it has a number or an address of its own, you can set it to answer anyone who writes, or only contacts it already knows.",
                    "Contacts never sign in.",
                    "A contact is answered only from what the Lemma has marked Public. Nothing else it keeps is used.",
                    "What it cannot answer is passed to the person who looks after its contacts.",
                    "Each contact has a conversation of their own. Contacts lists them for whoever looks after them.",
                ],
            },
            {
                type: "links",
                title: "Related",
                items: [
                    {
                        label: "Where to reach a Lemma",
                        href: "/docs/use/reach",
                        note: "The places people and contacts write to it.",
                    },
                    {
                        label: "Connectors",
                        href: "/docs/use/connectors",
                        note: "Whose account a Lemma uses in other apps.",
                    },
                ],
            },
        ],
    },
    {
        slug: "use/connectors",
        title: "Connectors",
        eyebrow: USE,
        group: USE,
        description:
            "A connector lets a Lemma work in the apps your work already lives in, such as Gmail, Slack or GitHub, using an account you connected.",
        blocks: [
            {
                type: "table",
                title: "Three parts",
                body: "Keeping these apart is what keeps your account yours.",
                columns: ["Part", "Who does it", "What it is"],
                rows: [
                    [
                        "The connector",
                        "An owner or editor of the organization, once",
                        "The organization decides it uses an app, and how to sign in to it.",
                    ],
                    [
                        "Your account",
                        "Each person",
                        "Your own sign-in to that app. It belongs to you.",
                    ],
                    [
                        "The Lemma’s connection",
                        "Someone who can edit the Lemma",
                        "The Lemma says it uses the connector, and whose account its work runs as.",
                    ],
                ],
            },
            {
                type: "steps",
                title: "Connect your account",
                items: [
                    "Choose Connectors in the sidebar. It lists what is connected and what more you can connect.",
                    "Choose Connect on a service.",
                    "Sign in on that service’s own screen. Lemma never asks for your password to that service.",
                    "You come back to where you started, connected.",
                ],
            },
            {
                type: "paragraph",
                body: "Where a service says “An owner or editor sets this up”, ask one of them. Owners and editors can also add your organization’s own servers, APIs and databases under “Your own”.",
            },
            {
                type: "steps",
                title: "Give a Lemma a connector",
                items: [
                    "Open the Lemma’s About tab and choose “Its connections”.",
                    "Choose “Add a connector” and pick the app.",
                    "Choose whose account it runs as: “Each person’s own”, or “One shared account”.",
                    "Choose Add. For “Each person’s own”, connect yours when asked.",
                ],
            },
            {
                type: "table",
                title: "Whose account it runs as",
                columns: ["Choice", "What happens"],
                rows: [
                    [
                        "Each person’s own",
                        "Work runs as whoever is asking, or the person the Lemma is working for. If you have not connected an account, it stops and gives you a link to connect one. It never uses someone else’s.",
                    ],
                    [
                        "One shared account",
                        "Work runs as one account for everyone. Only the owner of an account can share it with the Lemma. When they take it back, that connector stops until an account is shared again.",
                    ],
                ],
            },
            {
                type: "list",
                title: "What to expect",
                items: [
                    "Your sign-in is encrypted and is never shown back to anyone, including you.",
                    "Nobody else can use your account, unless you share it with a Lemma, and then only that Lemma can.",
                    "A Lemma uses only the connectors it was given.",
                    "Before it changes something in a connected app, as opposed to reading it, the Lemma asks you, unless that was allowed in advance.",
                    "When an account stops working, Lemma marks it and shows you, so you can reconnect it before something fails.",
                    "Remove your account and nothing in the organization can act as you in that app. It leaves every Lemma it was used in.",
                    "Before an owner or editor removes a connector from the organization, Lemma says which Lemmas and how many accounts will stop working.",
                    "A Lemma you export or share never carries accounts or passwords. The person who brings it in connects their own.",
                ],
            },
            {
                type: "links",
                title: "Related",
                items: [
                    {
                        label: "Automations",
                        href: "/docs/use/automations",
                        note: "Start work when a connected app reports something.",
                    },
                    {
                        label: "Connectors in the CLI",
                        href: "/docs/cli/connectors",
                        note: "For developers.",
                    },
                ],
            },
        ],
    },
];

const guideOnGithub = (file: string) => `${githubUrl}/blob/main/docs/${file}`;

export const runItYourselfPage: DocsPage = {
    slug: "run-it-yourself",
    title: "Run it yourself",
    eyebrow: SET_UP,
    group: SET_UP,
    description:
        "Lemma is open source. Use it on Lemma Cloud, run all of it on your Mac, or put it on a server you control.",
    blocks: [
        {
            type: "table",
            title: "Three ways to run Lemma",
            columns: ["Way", "Choose it when", "What you need"],
            rows: [
                [
                    "Lemma Cloud",
                    "You want to start now and host nothing.",
                    "An account at lemma.work.",
                ],
                [
                    "The Mac app, in Local mode",
                    "You want Lemma and your data on your own machine.",
                    "macOS 14 on Apple silicon, and about 12 GB free.",
                ],
                [
                    "Your own server",
                    "You run Lemma for a team on a machine you control.",
                    "A Linux machine with Docker, and a key for an AI model provider.",
                ],
            ],
        },
        {
            type: "paragraph",
            title: "Lemma Cloud",
            body: "Sign up at lemma.work and make your first Lemma. There is nothing to install.",
        },
        {
            type: "steps",
            title: "On your Mac",
            body: "The app installs what it needs. You do not need Docker, a database or anything else.",
            items: [
                "Download Lemma from lemma.work/download, open the file and drag Lemma into Applications.",
                "Open Lemma, choose “Use Local Lemma”, then “Install local services”. The first start downloads the rest and takes a few minutes.",
                "When it says it is ready, choose “Create account”.",
                "Add an AI model: pick a provider and enter its key. Nothing a Lemma does works without one. With Ollama or LM Studio running on the same Mac, no key is needed.",
            ],
        },
        {
            type: "list",
            title: "What Local mode means",
            items: [
                "Your data stays on your Mac. The AI provider and the connectors you set up still receive what you send them.",
                "Only you can open it until you turn on Sharing, under Settings, This Mac. Sharing is by invitation unless you change it.",
                "Closing the window keeps Lemma running in the menu bar, so automations still run. Quitting stops everything.",
                "Email, connectors and the places people reach a Lemma each need a key or an account of your own. Settings, This Mac, Server setup lists them and tests each one.",
            ],
        },
        {
            type: "paragraph",
            title: "On your own server",
            body: "Lemma for a team runs on one Linux machine with Docker: 4 CPUs, 8 GB of memory and 40 GB of disk. You bring a key for an AI model provider, and a domain if you want one. Keep the machine for Lemma alone. The self-hosting guide has the commands and what to set first.",
        },
        {
            type: "links",
            title: "Guides on GitHub",
            body: "The full instructions are kept with the code.",
            items: [
                {
                    label: "Installation",
                    href: guideOnGithub("installation.md"),
                    note: "The Mac app step by step: models, email, sharing, updates and removing it.",
                },
                {
                    label: "Self-hosting",
                    href: guideOnGithub("self-hosting.md"),
                    note: "Lemma on your own server, with your own domain.",
                },
                {
                    label: "Configuration",
                    href: guideOnGithub("configuration.md"),
                    note: "The settings you set to run Lemma, and what each one decides.",
                },
                {
                    label: "Observability",
                    href: guideOnGithub("observability.md"),
                    note: "Send traces, metrics and logs to your own collector.",
                },
            ],
        },
        {
            type: "links",
            title: "Related",
            items: [
                {
                    label: "Download Lemma",
                    href: "/download",
                    note: "The Mac app.",
                },
                {
                    label: "Quickstart",
                    href: "/docs/getting-started",
                    note: "For developers: install the CLI and point it at any of the three.",
                },
            ],
        },
    ],
};

/** The two groups these pages sit in, first in the guide list. */
export const useLemmaGroups: DocsGroup[] = [
    {
        title: USE,
        door: "use",
        pages: useLemmaPages.map((page) => page.slug),
    },
    { title: SET_UP, door: "use", pages: [runItYourselfPage.slug] },
];
