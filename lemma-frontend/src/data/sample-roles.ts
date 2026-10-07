/** The shelf the sample workspace shows: `GET /pods/bundle/templates` as the
 *  backend answers it, copied, because the sample has no server to ask.
 *
 *  A copy can drift from the templates it was taken from, so
 *  `tests/roles.test.ts` holds every card here to its template's `pod.json`
 *  and scorecard. When a template changes, change this to match — or take a
 *  fresh copy of the endpoint's answer. */
export const SAMPLE_ROLES = {
    "items": [
        {
            "template": "support-desk",
            "name": "Support desk",
            "role": "Answers customers and knows when to hand over",
            "about": "Answers customers and knows when to hand over.",
            "seed": "arch/reception/13",
            "brings": [
                "Skills for answering, handing over and filing bugs",
                "Conversations, known issues and callbacks, as tables",
                "A scorecard you check every Friday"
            ],
            "wins": [
                {
                    "say": "Here is our help center. Tell me the five questions it answers worst."
                },
                {
                    "say": "Draft replies to the open conversations in Intercom. Don’t send anything.",
                    "needs": {
                        "connector": "intercom",
                        "label": "Intercom"
                    }
                },
                {
                    "say": "Read this week’s support channel and start the known issues list.",
                    "needs": {
                        "connector": "slack",
                        "label": "Slack"
                    }
                }
            ],
            "offers": [
                {
                    "title": "Morning list",
                    "detail": "Each weekday, what came in overnight that needs a person",
                    "cron": "0 9 * * 1-5",
                    "instruction": "List the customer conversations that came in since yesterday evening and need a person, newest first, one line each with a link. Draft nothing and send nothing to customers."
                },
                {
                    "title": "Friday themes",
                    "detail": "Each Friday, what customers asked about most",
                    "cron": "0 15 * * 5",
                    "instruction": "Write what customers asked about most this week, with counts and links, and add any problem reported more than once to known_issues using the bug-report skill."
                }
            ],
            "judged_on": [
                "Known issues have a tracker issue",
                "Callbacks happen by their date",
                "First reply inside an hour",
                "Closed without handing over",
                "Median time to first reply"
            ],
            "skills": [
                {
                    "name": "answer-from-help-center",
                    "description": "Answer a customer's question from the team's own help center and past replies. Use when a customer asks how something works, why something happened, or what to do next."
                },
                {
                    "name": "bug-report",
                    "description": "Turn customer reports of a problem into a bug report engineering can act on, and keep the known issues list current. Use when a customer reports something broken, or when the same problem shows up in more than one conversation."
                },
                {
                    "name": "hand-over",
                    "description": "Hand a customer conversation to a person with the whole story, so nobody has to ask the customer again. Use when a question needs a decision, an exception, money, or knowledge the team has not written down."
                }
            ],
            "tables": [
                {
                    "name": "callbacks",
                    "description": "Customers someone promised to get back to, and by when."
                },
                {
                    "name": "conversations",
                    "description": "Customer conversations: when each started, when it was first answered, and how it ended."
                },
                {
                    "name": "known_issues",
                    "description": "Problems customers report more than once, with the tracker issue that owns each."
                }
            ]
        },
        {
            "template": "follow-ups",
            "name": "Follow-ups",
            "role": "Keeps track of commitments and prepares follow-ups",
            "about": "Keeps track of commitments and prepares follow-ups.",
            "seed": "arch/follow-ups/6",
            "brings": [
                "A shared list of commitments",
                "Skills for tracking promises and drafting follow-ups",
                "A scorecard you check every Friday"
            ],
            "wins": [
                {
                    "say": "Here is everything I said I would do this week."
                },
                {
                    "say": "What have I promised someone that has gone quiet?"
                },
                {
                    "say": "Sweep every weekday morning and tell me what has gone cold."
                }
            ],
            "offers": [
                {
                    "title": "Morning sweep",
                    "detail": "Each weekday, what has gone cold and what is due",
                    "cron": "0 9 * * 1-5",
                    "instruction": "Read the open rows in commitments. List what is overdue, then what is due in the next two days, owner by owner, and draft a follow-up for anything a week late using the draft-follow-up skill. Send nothing yourself."
                }
            ],
            "judged_on": [
                "Follow-ups go out before they go cold",
                "Promises still open after their date",
                "Promises closed within two days of their date"
            ],
            "skills": [
                {
                    "name": "draft-follow-up",
                    "description": "Draft a follow-up for a commitment that is due or going cold, for the owner to send. Use when a commitment is due within two days, is overdue, or has had no reply for a week."
                },
                {
                    "name": "track-commitments",
                    "description": "Keep the commitments table true to what the team has promised. Use when someone says they will do something for someone, when a promise is kept or dropped, or when asked what is open."
                }
            ],
            "tables": [
                {
                    "name": "commitments",
                    "description": "What the team promised, to whom, by when, and whether the follow-up went out."
                }
            ]
        },
        {
            "template": "content",
            "name": "Content",
            "role": "Plans, drafts and posts your short videos on schedule",
            "about": "Plans, drafts and posts short videos on a schedule, and reports how each one did.",
            "seed": "arch/content/28",
            "brings": [
                "Skills for planning posts, writing scripts and checking how they did",
                "A posts table: planned, drafted, posted, and views after two days",
                "A scorecard you check every Friday"
            ],
            "wins": [
                {
                    "say": "Here are three accounts I like. Tell me what makes their posts work."
                },
                {
                    "say": "Read my last ten posts and tell me which worked and why.",
                    "needs": {
                        "connector": "instagram",
                        "label": "Instagram"
                    }
                },
                {
                    "say": "Plan next week’s posts and put them in the posts table."
                }
            ],
            "offers": [
                {
                    "title": "Monday plan",
                    "detail": "Each Monday, the week’s posts planned and scripted",
                    "cron": "0 9 * * 1",
                    "instruction": "Plan this week's posts using the plan-posts skill: one row per post in the posts table with a title, a planned time and the sources. Draft each script with the write-script skill. Post nothing; every draft waits for a person."
                },
                {
                    "title": "Two-day check",
                    "detail": "Each morning, views for what went out two days ago",
                    "cron": "0 10 * * *",
                    "instruction": "Use the check-how-it-did skill on every post published about 48 hours ago: record views_48h on its row, and say in one line what the best and worst of them did differently."
                }
            ],
            "judged_on": [
                "Posts go out by their planned time",
                "Posts you approve without changes",
                "Median views two days after posting"
            ],
            "skills": [
                {
                    "name": "check-how-it-did",
                    "description": "Record how a post did two days after it went out, and say why. Use for posts published about 48 hours ago, or when asked which posts worked."
                },
                {
                    "name": "plan-posts",
                    "description": "Plan the week's posts and keep the posts table true to them. Use when asked to plan posts, when a planned time changes, or when a post goes out."
                },
                {
                    "name": "write-script",
                    "description": "Draft the script and caption for a planned post from its sources. Use when a post is planned and has no draft yet, or when asked to rewrite one."
                }
            ],
            "tables": [
                {
                    "name": "posts",
                    "description": "One row per post: what it is, when it was planned for, when it went out, whether it went out as drafted, and how many views it had two days later."
                }
            ]
        },
        {
            "template": "software-factory",
            "name": "Software factory",
            "role": "Turns tagged issues into pull requests your team reviews",
            "about": "Turns issues tagged for it into pull requests your team reviews, and learns how your team likes code written.",
            "seed": "arch/software-factory/1",
            "brings": [
                "Skills for working an issue and answering review",
                "A pull requests table: opened, review rounds, merged, reverted",
                "A scorecard you check every Friday"
            ],
            "wins": [
                {
                    "say": "Here is our repo. Tell me how a change gets from issue to merged here.",
                    "needs": {
                        "connector": "github",
                        "label": "GitHub"
                    }
                },
                {
                    "say": "Read our last twenty merged pull requests and write down how we like code reviewed.",
                    "needs": {
                        "connector": "github",
                        "label": "GitHub"
                    }
                },
                {
                    "say": "Take the smallest issue tagged for you and open a pull request."
                }
            ],
            "offers": [
                {
                    "title": "Morning pick-up",
                    "detail": "Each weekday, one tagged issue taken to a pull request",
                    "cron": "0 9 * * 1-5",
                    "instruction": "Take the oldest open issue tagged for you that nobody is working on, and use the work-an-issue skill to open a pull request for it. Never merge, and never push to the main branch."
                },
                {
                    "title": "Review replies",
                    "detail": "Each weekday afternoon, answers to review comments",
                    "cron": "0 14 * * 1-5",
                    "instruction": "Use the answer-review skill on every open pull request of yours with review comments since your last reply. Never merge."
                }
            ],
            "judged_on": [
                "Merged without a second round of changes",
                "Median time from opened to merged",
                "Merged changes that stayed merged"
            ],
            "skills": [
                {
                    "name": "answer-review",
                    "description": "Answer review comments on your pull requests and push the fixes. Use when one of your pull requests has new review comments or a requested change."
                },
                {
                    "name": "work-an-issue",
                    "description": "Take an issue tagged for you to a pull request the team can review, and keep its row in pull_requests. Use when picking up an issue or when asked to fix something in the code."
                }
            ],
            "tables": [
                {
                    "name": "pull_requests",
                    "description": "One row per pull request: the issue it came from, when it was opened and merged, how many rounds of review it took, and whether it was reverted."
                }
            ]
        },
        {
            "template": "books",
            "name": "Books",
            "role": "Keeps the books reconciled and chases unpaid invoices",
            "about": "Keeps the books reconciled and chases unpaid invoices. Proposes every correction; a person approves it.",
            "seed": "arch/books/12",
            "brings": [
                "Skills for reconciling bank lines and chasing invoices",
                "Bank lines and invoices, as tables",
                "A scorecard you check every Friday"
            ],
            "wins": [
                {
                    "say": "Here is last month’s bank statement. Tell me what will not tie."
                },
                {
                    "say": "List every unpaid invoice older than its terms, biggest first.",
                    "needs": {
                        "connector": "xero",
                        "label": "Xero"
                    }
                },
                {
                    "say": "Draft a polite reminder for each overdue invoice. Don’t send anything."
                }
            ],
            "offers": [
                {
                    "title": "Morning reconcile",
                    "detail": "Each weekday, yesterday’s bank lines matched",
                    "cron": "0 8 * * 1-5",
                    "instruction": "Use the reconcile skill on every bank line booked since the last run. Propose a match for each; change no figure and post nothing without a person's yes."
                },
                {
                    "title": "Overdue chase",
                    "detail": "Each weekday, a reminder drafted for anything newly overdue",
                    "cron": "0 10 * * 1-5",
                    "instruction": "Use the chase-invoices skill on every invoice that went past its due date since yesterday. Draft the reminder for a person to send; send nothing yourself."
                }
            ],
            "judged_on": [
                "Bank lines reconciled within three days",
                "Invoices paid by their due date",
                "Overdue invoices with no reminder the next day"
            ],
            "skills": [
                {
                    "name": "chase-invoices",
                    "description": "Keep the invoices table current and draft reminders for overdue invoices. Use when an invoice is sent, paid or goes past its due date, or when asked who owes what."
                },
                {
                    "name": "reconcile",
                    "description": "Match bank lines to invoices, bills and transfers, and keep the bank_lines table true. Use when new bank lines arrive or when asked what will not tie."
                }
            ],
            "tables": [
                {
                    "name": "bank_lines",
                    "description": "One row per bank line: the account, the amount, when it was booked, and when it was reconciled."
                },
                {
                    "name": "invoices",
                    "description": "One row per invoice the team sent: who owes it, how much, when it was due, when it was paid, and when a reminder went out."
                }
            ]
        }
    ]
} as const;
