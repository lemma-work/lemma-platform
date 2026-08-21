import { COMPANY_DESCRIPTION, COMPANY_LEGAL_NAME, COMPANY_SHORT_NAME } from '@/lib/company';
import { config } from '@/lib/config';

export type LegalListItem = {
    /** Short lead-in set in ink, so a scanning reader can find the right item
     *  without reading five sentences to discover which one they are in. */
    label?: string;
    text: string;
    children?: string[];
};

export type LegalSection = {
    title: string;
    body?: string;
    items?: LegalListItem[];
};

/**
 * A question in the words someone would actually use, answered in one or two
 * before the policy explains itself.
 *
 * Nobody arrives at a privacy page to read a privacy page. They arrive with a
 * specific worry — usually "do you train on my stuff" — and the honest thing is
 * to answer it in the first screen rather than make them earn it through nine
 * numbered sections.
 */
export type LegalAnswer = {
    question: string;
    /** Two or three words. If it needs a sentence it belongs in `detail`. */
    answer: string;
    detail: string;
};

/**
 * The shape `LegalPage`, and the markdown renderer in lib/markdown/render.ts,
 * both know how to lay out. `LegalDocument` is the legal-specific case — an
 * effective date and a "short version" summary always apply to a policy — but
 * a page like About or Contact has real content in the same shape without
 * either, so those fields live here as optional rather than forcing every
 * structured page through the vocabulary of a policy.
 */
export type PageDocument = {
    title: string;
    description: string;
    effectiveDate?: string;
    summary?: string[];
    answers?: LegalAnswer[];
    sections: LegalSection[];
};

export type LegalDocument = PageDocument & {
    effectiveDate: string;
    summary: string[];
};

export const privacyPolicy: LegalDocument = {
    title: 'Privacy',
    description:
        'What Lemma collects, what it never collects, who else can see it, and how to change your mind — in plain language, because you should be able to read it.',
    effectiveDate: 'August 16, 2026',
    summary: [
        'We collect what it takes to run Lemma: who you are, what your workspace holds, and how the product gets used.',
        'We never sell it, never advertise against it, and never feed what you build into analytics.',
        'Run Lemma on your own machine or your own server and almost none of this happens at all.',
    ],
    answers: [
        {
            question: 'Do you sell my data?',
            answer: 'No.',
            detail: 'Not to advertisers, not to data brokers, not to anyone. Lemma is paid for by the people who use it.',
        },
        {
            question: 'Do you train models on my work?',
            answer: 'No.',
            detail: 'We do not train models on anything in your workspace, and we buy model capacity under business terms that do not let providers train on what we send them.',
        },
        {
            question: 'Does my workspace ever leave Lemma?',
            answer: 'When an agent runs.',
            detail: 'Agents are language models we do not host. What an agent needs to answer you goes to a model provider and comes back as a reply. Section 05 is the whole of it.',
        },
        {
            question: 'Can Lemma staff read my pods?',
            answer: 'Almost never.',
            detail: 'Only for support you have asked for, a security or abuse investigation, or a legal obligation. Access is limited to the people who need it and it is logged. Never to browse.',
        },
        {
            question: 'Am I tracked across the web?',
            answer: 'No.',
            detail: 'No ad networks, no third-party trackers, no session recording, no screen capture. One analytics tool, on servers in the EU, that never sees inside your workspace.',
        },
        {
            question: 'Can I change my mind?',
            answer: 'Any time.',
            detail: 'Analytics has a switch further down this page and it works in both directions. Turning it off removes what was stored in this browser.',
        },
    ],
    sections: [
        {
            title: 'Who we are',
            body: `Lemma is made by ${COMPANY_DESCRIPTION} ("we", "us"). This policy covers the Lemma website, the hosted product, the desktop app, our APIs, and the support conversations around them. It is meant to be read — if a line here is unclear or seems to contradict what the product actually does, that is a bug, and ${config.SUPPORT_EMAIL} is where to report it.`,
        },
        {
            title: 'What we collect',
            body: 'Four kinds of information, and it is worth knowing which is which, because they are used for very different things.',
            items: [
                {
                    label: 'Your account',
                    text: 'Your name, email address, organization, how you signed in, and your preferences. You hand us this in order to have an account.',
                },
                {
                    label: 'What you build',
                    text: 'The pods, tables, records, files, agents, workflows, and connections you create. We store it because storing it is the product. We do not read it for any other purpose.',
                },
                {
                    label: 'How the product gets used',
                    text: 'Which screens are opened, which actions succeed or fail, and the technical details every browser sends: device and browser type, IP address, the rough region that IP implies, timestamps.',
                },
                {
                    label: 'Payments',
                    text: 'Which plan you are on and what you have been billed. Card numbers go straight to our payment processor and are never held by us.',
                },
                {
                    label: 'What you tell us',
                    text: 'Emails, bug reports, feedback, survey answers, and anything else you send our way.',
                },
            ],
        },
        {
            title: 'What we never collect',
            body: 'This section matters more than the one above it, so it gets to be its own section rather than a caveat inside one.',
            items: [
                {
                    text: 'The contents of your workspace, for analytics. Records, files, prompts, and agent conversations are left out where events are created, not filtered out afterwards — the difference being that a filter is one mistake away from failing and an omission is not.',
                },
                {
                    text: 'The names of things you make. A pod called "Q3 layoffs" reaches our analytics as an identifier and nothing else.',
                },
                {
                    text: 'Your screen or your session. No session replay, and no autocapture — the feature that quietly scrapes the text off a page into event properties — because Lemma renders your business data and that is the fastest way to put it somewhere it does not belong.',
                },
                {
                    text: 'Identifiers in page addresses. A URL is reduced to its route pattern before it leaves your browser, so the id of the pod you were looking at is not carried out with the pageview.',
                },
                {
                    text: 'Anything bought from a data broker. We do not enrich, append, or buy information about you, and there is no advertising profile to build because we do not advertise against you.',
                },
            ],
        },
        {
            title: 'How we use it',
            body: 'Six purposes. If we ever want to use your information for something that is not on this list, we will come back and change the list first.',
            items: [
                { text: 'Run the product: sign you in, keep your session, store your work, and make the workspace do what it says on the tin.' },
                { text: 'Take payment: process purchases, manage subscriptions, send receipts, and sort out billing questions.' },
                { text: 'Keep it standing: watch performance, chase bugs, investigate abuse, and stop people getting into accounts that are not theirs.' },
                { text: 'Make it better: understand which parts are used, which parts are abandoned, and what to build next.' },
                { text: 'Talk to you: service notices, security alerts, policy changes, and answers to things you asked.' },
                { text: 'Meet obligations: comply with the law and enforce our terms when we have to.' },
            ],
        },
        {
            title: 'Language models',
            body: 'Lemma runs on large language models and we do not run those ourselves. This is the one place your workspace content routinely leaves our systems, so it is spelled out rather than folded into a list about vendors.',
            items: [
                {
                    text: 'When an agent runs, the parts of your workspace it needs in order to answer — your instructions, the records and files in scope, the conversation so far — are sent to a model provider, which sends a reply back. Nothing else goes with it.',
                },
                {
                    text: 'We contract with those providers on business terms that do not permit training on what we send. We do not train models on your workspace either, and we have no plans that would require us to.',
                },
                {
                    text: 'Connectors work the same way pointing outwards. Connect Lemma to another service, or ask an agent to send something somewhere, and data moves — on that service\'s terms as well as ours.',
                },
                {
                    text: 'If you run Lemma yourself, the provider is your choice, including a model running on hardware you own. Nothing in the product requires ours.',
                },
            ],
        },
        {
            title: 'Product analytics',
            body: 'On Lemma Cloud we measure how the product gets used so we can tell what is working. It is handled by PostHog, acting for us, on servers in the European Union. It is the smallest version of this we could build and still learn anything.',
            items: [
                {
                    label: 'What goes in',
                    text: 'That something happened and roughly where — a pod created, an agent run finished, a workflow completed — with the identifiers of the account, organization, and pod involved, and coarse buckets such as how long it took.',
                },
                {
                    label: 'What stays out',
                    text: 'Everything in "What we never collect" above. It is held there by an allowlist that drops any field not explicitly named, rather than by a list of forbidden fields, and by tests that try to smuggle a prompt and an email address through and fail if either survives.',
                },
                {
                    label: 'Before you answer',
                    text: 'Nothing is written to your device. That first visit is measured in memory and disappears when you close the tab, which is why the question can wait until you have had a look around.',
                },
                {
                    label: 'After you answer',
                    text: 'Saying yes stores one identifier in this browser so your visits join up. Saying no leaves nothing behind and keeps every visit unlinked. Either way we ask once and remember the answer.',
                },
            ],
        },
        {
            title: 'Who else sees it',
            body: 'We do not sell your information. It reaches other hands in five situations, and this is all five of them.',
            items: [
                {
                    label: 'Vendors who help us run Lemma',
                    text: 'Hosting, databases, email delivery, payments, error monitoring, analytics, model providers. Each one may use what it receives only to do that job for us, and no other.',
                },
                {
                    label: 'Your own administrators',
                    text: 'If you use Lemma through an organization, its admins can see and manage the account and workspace information their team holds. Your employer\'s Lemma is your employer\'s — worth knowing before you keep something personal in it.',
                },
                {
                    label: 'Services you connect',
                    text: 'A connector you set up, or a message you ask an agent to send, moves data outwards on purpose. That is the feature working.',
                },
                {
                    label: 'Courts and law enforcement',
                    text: 'When we are legally required, or when it is genuinely necessary to stop someone being harmed. We will tell you when a request touches your data unless we are legally barred from doing so.',
                },
                {
                    label: 'An acquirer',
                    text: 'If the company is ever sold, merged, or financed against, information can move as part of that. The policy travels with it, and you would hear from us before anything about it changed.',
                },
            ],
        },
        {
            title: 'Where it lives, and how long',
            body: 'Lemma Cloud runs on infrastructure in the European Union, and our analytics processor is in the European Union too. We are a US company, so some information reaches the United States — for support, billing, and engineering — under the standard contractual clauses and equivalent safeguards. We keep information while your account is live and while we still need it: to run the service, settle a bill, meet a legal obligation, or resolve a dispute. Delete something in Lemma and it leaves the live product straight away; copies can sit in encrypted backups for a limited window before they age out. Everything is encrypted in transit and at rest, access is scoped to the people who need it, and no one can promise perfect security — so we will not.',
        },
        {
            title: 'What you can do',
            items: [
                {
                    text: 'Switch analytics off, or back on, using the control on this page. Turning it off removes the identifier stored in this browser rather than merely stopping it being updated.',
                },
                {
                    text: `Get a copy of your information, correct it, or ask us to delete it — from workspace settings, or by writing to ${config.SUPPORT_EMAIL}. The EU, the UK, and California give people these rights by law; we extend them to everyone, because sorting users by passport is a strange way to run a company.`,
                },
                {
                    text: 'Unsubscribe from anything we send that is not about your account or your bill. The link is at the bottom of every one of those emails.',
                },
                {
                    text: 'Disconnect a connector, or delete what you have built, subject to what your team\'s permissions allow.',
                },
                {
                    text: 'Complain. To us first, we would hope — and if that goes nowhere, to your local data protection authority, which you are entitled to do without asking us.',
                },
            ],
        },
        {
            title: 'Running Lemma yourself',
            body: 'Lemma is open source and the whole thing runs on your own machine or your own server. When it does, most of this policy stops applying, because we mostly stop being involved. A local-first product that quietly phones home would have lost something it could not buy back, so the reporting is deliberately close to nothing.',
            items: [
                {
                    text: 'A self-hosted server sends one anonymous heartbeat: a random instance identifier, the version, and a bucketed count of pods. No pod identifiers, no names, no content. It can be switched off.',
                },
                {
                    text: 'Lemma Desktop in local mode sends install health only — whether the runtime installed and how long it took — against a random install identifier, so we hear about a broken installer from the installer rather than from a GitHub issue three weeks later. It can be switched off.',
                },
                {
                    text: 'Product analytics does not run on either one. There is no key configured to run it with, and the code path that would send it is not merely disabled but absent.',
                },
                {
                    text: 'Your data stays on your hardware, and the model provider is yours to pick.',
                },
            ],
        },
        {
            title: 'Children',
            body: 'Lemma is built for work and is not directed at children. We do not knowingly collect information from anyone under 16. If you believe a child has given us information, write to us and we will delete it.',
        },
        {
            title: 'When this changes',
            body: `We will update this page as the product changes. The effective date at the top moves whenever it does, and for anything material — a new purpose, a new category of recipient, anything that would change a reasonable person's mind — we will tell you in the product or by email before it takes effect, rather than quietly reissuing the page and hoping.`,
        },
        {
            title: 'Contact',
            body: `Lemma is a product of ${COMPANY_LEGAL_NAME} Privacy questions, data requests, and complaints all go to ${config.SUPPORT_EMAIL}, and a person reads them.`,
        },
    ],
};

export const termsOfService: LegalDocument = {
    title: 'Terms of Service',
    description:
        'These terms govern your access to Lemma websites, hosted product surfaces, APIs, and related services.',
    effectiveDate: 'July 31, 2026',
    summary: [
        'Use Lemma lawfully and only in ways you are authorized to use it.',
        'You are responsible for your account, your workspace activity, and the content you bring into the product.',
        'Paid features, suspensions, and service changes may also be governed by separate order forms or plan terms.',
    ],
    sections: [
        {
            title: 'Using Lemma',
            body: `These terms are an agreement between you and ${COMPANY_DESCRIPTION}, which operates Lemma. By accessing or using Lemma, you agree to them. If you are using the service on behalf of an organization, you represent that you have authority to bind that organization to these terms.`,
        },
        {
            title: 'Accounts and Workspace Responsibility',
            body: 'You are responsible for maintaining the confidentiality of your login credentials and for activity that occurs under your account. Organizations and workspace admins are responsible for managing member access, permissions, and connected tools within their workspace.',
        },
        {
            title: 'Acceptable Use',
            body: 'You may not use Lemma to violate the law, infringe the rights of others, interfere with the service, or abuse the platform.',
            items: [
                { text: 'Do not attempt to gain unauthorized access to accounts, data, systems, or networks.' },
                { text: 'Do not upload or transmit malicious code, malware, spam, or harmful automated traffic.' },
                { text: 'Do not use the service to infringe intellectual property, privacy, publicity, or contractual rights.' },
                { text: 'Do not use Lemma in ways that could damage, disable, overload, reverse engineer, or undermine the product or other users.' },
            ],
        },
        {
            title: 'Customer Content and Permissions',
            body: 'You retain your rights to the prompts, files, data, and other content you or your team submit to Lemma. You grant us the limited rights needed to host, process, transmit, back up, and display that content in order to operate and improve the service for you.',
        },
        {
            title: 'Plans, Billing, and Changes',
            body: 'Some parts of Lemma may require a paid subscription or separate commercial agreement. If you purchase a paid plan, you agree to the pricing, billing cycle, and payment terms presented at the time of purchase or in a signed order form. Unless stated otherwise, fees are non-refundable.',
        },
        {
            title: 'Our Service and Intellectual Property',
            body: `Lemma and its related software, designs, interfaces, branding, and documentation are owned by ${COMPANY_LEGAL_NAME} or its licensors and are protected by applicable law. These terms do not grant you ownership of the service itself, only the limited right to use it under these terms.`,
        },
        {
            title: 'Suspension and Termination',
            body: 'We may suspend or terminate access if we reasonably believe your use violates these terms, creates security or legal risk, harms other users, or threatens the integrity of the service. You may stop using Lemma at any time.',
        },
        {
            title: 'Disclaimers and Limitation of Liability',
            body: `Lemma is provided on an “as is” and “as available” basis to the fullest extent permitted by law. To the fullest extent permitted by law, ${COMPANY_LEGAL_NAME} will not be liable for indirect, incidental, special, consequential, exemplary, or punitive damages, or for loss of profits, revenues, data, goodwill, or business opportunities arising from or related to your use of the service.`,
        },
        {
            title: 'Updates to the Service or Terms',
            body: 'We may update the service and these terms from time to time. If we make material changes, we will update the effective date above and may provide additional notice when appropriate. Continued use of Lemma after the updated terms take effect means you accept the revised terms.',
        },
        {
            title: 'Contact',
            body: `Lemma is a product of ${COMPANY_SHORT_NAME}. Questions about these terms can be sent to ${config.SUPPORT_EMAIL}.`,
        },
    ],
};
