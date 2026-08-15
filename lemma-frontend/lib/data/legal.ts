import { COMPANY_DESCRIPTION, COMPANY_LEGAL_NAME } from '@/lib/company';
import { config } from '@/lib/config';

export type LegalListItem = {
    text: string;
    children?: string[];
};

export type LegalSection = {
    title: string;
    body?: string;
    items?: LegalListItem[];
};

export type LegalDocument = {
    title: string;
    description: string;
    effectiveDate: string;
    summary: string[];
    sections: LegalSection[];
};

export const privacyPolicy: LegalDocument = {
    title: 'Privacy Policy',
    description:
        'This page explains what information Lemma collects, why we use it, when we share it, and the choices you have when you use the product.',
    effectiveDate: 'July 31, 2026',
    summary: [
        'We collect the account, workspace, usage, and billing information needed to run Lemma reliably.',
        'We use that information to provide the service, secure it, improve it, and communicate with you.',
        'We only share information with service providers, your workspace administrators, or when required for legal or security reasons.',
    ],
    sections: [
        {
            title: 'What This Policy Covers',
            body: `Lemma is operated by ${COMPANY_DESCRIPTION} ("we", "us"). This privacy policy applies to Lemma websites, product surfaces, hosted workspaces, support interactions, and related communications. If your team uses Lemma through an organization account, your workspace administrators may also have access to certain account and usage information needed to operate the workspace.`,
        },
        {
            title: 'Information We Collect',
            body: 'We collect information in a few different ways depending on how you use the service.',
            items: [
                {
                    text: 'Account and profile information, such as your name, email address, organization name, login method, and basic account preferences.',
                },
                {
                    text: 'Workspace content and configuration, including prompts, files, tables, workflows, assistants, connectors, and other materials you or your team create inside Lemma.',
                },
                {
                    text: 'Usage and device information, such as browser type, IP address, approximate location, pages visited, referring URLs, timestamps, and product activity logs.',
                },
                {
                    text: 'Payment and transaction information when you purchase paid services. Payment card details are typically processed by our payment providers rather than stored directly by Lemma.',
                },
                {
                    text: 'Support and communication data, including emails, feedback, bug reports, survey responses, and other messages you send to us.',
                },
            ],
        },
        {
            title: 'How We Use Information',
            body: 'We use the information we collect to operate Lemma and keep it useful, secure, and reliable.',
            items: [
                { text: 'Provide the product, authenticate users, maintain sessions, and deliver workspace functionality.' },
                { text: 'Process purchases, manage subscriptions, send receipts, and support account administration.' },
                { text: 'Monitor performance, troubleshoot issues, detect abuse, and prevent fraud or unauthorized access.' },
                { text: 'Improve product quality, ship new features, understand usage patterns, and prioritize support.' },
                { text: 'Communicate service updates, policy changes, security notices, and support responses.' },
                { text: 'Comply with legal obligations and enforce our terms, policies, and other contractual rights.' },
            ],
        },
        {
            title: 'When We Share Information',
            body: 'We do not sell your personal information. We share information only in the limited situations below.',
            items: [
                {
                    text: 'With vendors and infrastructure providers that help us host, secure, analyze, support, or bill for the service.',
                },
                {
                    text: 'With your organization or workspace administrators, who may be able to view or manage account and workspace information associated with their team.',
                },
                {
                    text: 'With connector partners or third-party services when you choose to connect them to Lemma or direct us to send data to them.',
                },
                {
                    text: 'If required by law, legal process, or a valid government request, or when reasonably necessary to protect users, Lemma, or the public.',
                },
                {
                    text: 'As part of a merger, acquisition, financing, or sale of all or part of our business, subject to standard confidentiality and continuity protections.',
                },
            ],
        },
        {
            title: 'Product Analytics',
            body: 'On Lemma Cloud we measure how the product is used so we can see what works and what does not. This is handled by PostHog, acting as our processor, on infrastructure hosted in the European Union.',
            items: [
                {
                    text: 'What we send: which actions happened and how they arrived — a pod was created, an agent run finished, a workflow completed — along with the identifiers of the account, organization, and pod involved, and coarse buckets such as how long something took.',
                },
                {
                    text: 'What we never send: your workspace content. Table records, files, prompts, agent conversations, and the names of anything you create are excluded at the point of collection, not filtered afterwards. Page addresses are reduced to their route pattern before they leave the browser, so identifiers in a URL are not transmitted.',
                },
                {
                    text: 'Analytics is off entirely on self-hosted installations and on Lemma Desktop running locally, where no analytics key is configured and nothing is collected.',
                },
                {
                    text: 'Until you accept analytics, nothing is stored on your device: measurement for that session is held in memory only and is gone when you close the tab.',
                },
            ],
        },
        {
            title: 'Retention and Security',
            body: 'We retain information for as long as needed to provide the service, comply with legal obligations, resolve disputes, and enforce our agreements. We use reasonable administrative, technical, and organizational measures to protect information, but no system can be guaranteed completely secure.',
        },
        {
            title: 'Your Choices',
            body: 'Depending on how you use Lemma, you may have the ability to access, update, export, or delete certain information through your workspace settings or by contacting us.',
            items: [
                { text: 'You can decline product analytics when we ask, and declining is remembered — nothing is written to your device and no identifier persists between visits.' },
                { text: 'You can opt out of non-essential marketing emails using the unsubscribe link in those messages.' },
                { text: 'You can disconnect connectors or remove content you no longer want stored in a workspace, subject to your team permissions.' },
                { text: 'Workspace administrators may have separate controls over retained data, member access, and connected services.' },
            ],
        },
        {
            title: 'Changes to This Policy',
            body: 'We may update this privacy policy from time to time. If we make material changes, we will update the effective date above and may provide additional notice through the product or by email when appropriate.',
        },
        {
            title: 'Contact',
            body: `Lemma is a product of ${COMPANY_LEGAL_NAME} Questions about this policy can be sent to ${config.SUPPORT_EMAIL}.`,
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
            body: `Lemma is a product of ${COMPANY_LEGAL_NAME} Questions about these terms can be sent to ${config.SUPPORT_EMAIL}.`,
        },
    ],
};
