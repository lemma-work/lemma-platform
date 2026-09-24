export interface AppIdea { name: string; description: string; prompt: string; }
export interface AppCategory { id: string; name: string; previewRowSplit?: number; ideas: AppIdea[]; }

export const APP_CATEGORIES: AppCategory[] = [
    {
        "id": "personal",
        "name": "Personal",
        "ideas": [
            {
                "name": "Daily planner",
                "description": "Plan your day with a schedule and a focused task list.",
                "prompt": "Let's build a daily planner with time blocks, priorities and a task checklist. Help me adapt it to what I need."
            },
            {
                "name": "Habit tracker",
                "description": "Keep a daily record of the habits you want to build.",
                "prompt": "Let's build a habit tracker with daily check-ins, streaks and a monthly calendar. Help me adapt it to what I need."
            },
            {
                "name": "Reading list",
                "description": "Save books and articles, and keep your notes together.",
                "prompt": "Let's build a reading list for books and articles with progress, notes and tags. Help me adapt it to what I need."
            },
            {
                "name": "Travel planner",
                "description": "Keep your itinerary, bookings and places in one place.",
                "prompt": "Let's build a travel planner with a day-by-day itinerary, bookings and a map. Help me adapt it to what I need."
            }
        ]
    },
    {
        "id": "marketing",
        "name": "Marketing",
        "ideas": [
            {
                "name": "Content calendar",
                "description": "Plan posts across channels and track what is ready.",
                "prompt": "Let's build a content calendar with channels, drafts, publish dates and review status. Help me adapt it to what I need."
            },
            {
                "name": "Campaign tracker",
                "description": "Track campaign budgets, results and next steps.",
                "prompt": "Let's build a campaign tracker with budgets, goals, performance and next steps. Help me adapt it to what I need."
            },
            {
                "name": "Brand library",
                "description": "Find approved logos, images and brand guidelines.",
                "prompt": "Let's build a brand library for approved logos, images, colors and usage guidelines. Help me adapt it to what I need."
            },
            {
                "name": "Launch planner",
                "description": "Keep launch tasks, deadlines and approvals on track.",
                "prompt": "Let's build a launch planner with milestones, assets, owners and approvals. Help me adapt it to what I need."
            }
        ]
    },
    {
        "id": "product",
        "name": "Product",
        "ideas": [
            {
                "name": "Roadmap",
                "description": "Share what is being built now, next and later.",
                "prompt": "Let's build a product roadmap with now, next and later priorities and owners. Help me adapt it to what I need."
            },
            {
                "name": "Feedback inbox",
                "description": "Collect requests and spot recurring customer needs.",
                "prompt": "Let's build a feedback inbox with customer requests, tags, votes and follow-up status. Help me adapt it to what I need."
            },
            {
                "name": "Research library",
                "description": "Organize interviews, sources and research findings.",
                "prompt": "Let's build a research library with sources, interview notes, findings and searchable tags. Help me adapt it to what I need."
            },
            {
                "name": "Experiment tracker",
                "description": "Record hypotheses and compare experiment results.",
                "prompt": "Let's build an experiment tracker with hypotheses, variants, success metrics and results. Help me adapt it to what I need."
            }
        ]
    },
    {
        "id": "sales",
        "name": "Sales",
        "ideas": [
            {
                "name": "Deal pipeline",
                "description": "See where each deal stands and what happens next.",
                "prompt": "Let's build a deal pipeline with stages, deal values, owners and next actions. Help me adapt it to what I need."
            },
            {
                "name": "Customer tracker",
                "description": "Keep customer notes and follow-ups up to date.",
                "prompt": "Let's build a customer tracker with contacts, notes, next steps and follow-up reminders. Help me adapt it to what I need."
            },
            {
                "name": "Proposal builder",
                "description": "Put together proposals with scope and pricing.",
                "prompt": "Let's build a proposal builder with scope, pricing line items, reusable sections and review status. Help me adapt it to what I need."
            },
            {
                "name": "Sales forecast",
                "description": "Compare your pipeline with revenue targets.",
                "prompt": "Let's build a sales forecast with revenue targets, expected close dates and weighted deal values. Help me adapt it to what I need."
            }
        ]
    },
    {
        "id": "engineering",
        "previewRowSplit": 0.467,
        "name": "Engineering",
        "ideas": [
            {
                "name": "Issue tracker",
                "description": "Triage bugs and track fixes through to release.",
                "prompt": "Let's build an issue tracker with severity, assignees, reproduction steps and release status. Help me adapt it to what I need."
            },
            {
                "name": "Release checklist",
                "description": "Check what is ready before a deployment.",
                "prompt": "Let's build a release checklist with readiness checks, owners, approvals and version history. Help me adapt it to what I need."
            },
            {
                "name": "Incident log",
                "description": "Keep a record of incidents, decisions and follow-ups.",
                "prompt": "Let's build an incident log with timelines, impact, resolution notes and follow-up actions. Help me adapt it to what I need."
            },
            {
                "name": "Service dashboard",
                "description": "See service health and investigate changes.",
                "prompt": "Let's build a service dashboard with health checks, latency, uptime and links to investigations. Help me adapt it to what I need."
            }
        ]
    }
];
