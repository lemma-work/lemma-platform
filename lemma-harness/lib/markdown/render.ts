/**
 * Renders the same structured content the docs, privacy, and terms pages
 * already hold as markdown — for the `Accept: text/markdown` variant of
 * those routes. Source of truth stays the structured data in lib/data/; this
 * only chooses a plain-text shape for it, the way DocsPageView and LegalPage
 * choose a visual one.
 */
import type { DocsBlock, DocsPage } from '@/lib/data/docs';
import type { PageDocument } from '@/lib/data/legal';

function markdownTable(columns: string[], rows: string[][]): string {
    const header = `| ${columns.join(' | ')} |`;
    const divider = `| ${columns.map(() => '---').join(' | ')} |`;
    const body = rows.map((row) => `| ${row.join(' | ')} |`).join('\n');
    return [header, divider, body].join('\n');
}

function blockToMarkdown(block: DocsBlock): string {
    switch (block.type) {
        case 'paragraph':
            return [block.title ? `### ${block.title}` : null, block.body].filter(Boolean).join('\n\n');
        case 'list':
            return [
                `### ${block.title}`,
                block.body ?? null,
                block.items.map((item) => `- ${item}`).join('\n'),
            ]
                .filter(Boolean)
                .join('\n\n');
        case 'steps':
            return [
                `### ${block.title}`,
                block.body ?? null,
                block.items.map((item, index) => `${index + 1}. ${item}`).join('\n'),
            ]
                .filter(Boolean)
                .join('\n\n');
        case 'code':
            return [block.title ? `### ${block.title}` : null, block.body ?? null, `\`\`\`${block.language}\n${block.code}\n\`\`\``]
                .filter(Boolean)
                .join('\n\n');
        case 'table':
            return [block.title ? `### ${block.title}` : null, block.body ?? null, markdownTable(block.columns, block.rows)]
                .filter(Boolean)
                .join('\n\n');
        case 'callout':
            return `> **${block.title}**\n>\n> ${block.body}`;
    }
}

export function docsPageToMarkdown(page: DocsPage): string {
    return [`# ${page.title}`, page.description, ...page.blocks.map(blockToMarkdown)].join('\n\n') + '\n';
}

export function legalDocumentToMarkdown(document: PageDocument): string {
    const parts: string[] = [`# ${document.title}`, document.description];
    if (document.effectiveDate) {
        parts.push(`_Effective ${document.effectiveDate}_`);
    }

    if (document.summary && document.summary.length > 0) {
        parts.push(document.summary.map((line) => `- ${line}`).join('\n'));
    }

    if (document.answers && document.answers.length > 0) {
        parts.push(
            document.answers
                .map((answer) => `**${answer.question}** ${answer.answer} ${answer.detail}`)
                .join('\n\n')
        );
    }

    for (const section of document.sections) {
        const sectionParts = [`## ${section.title}`, section.body ?? null];
        if (section.items) {
            sectionParts.push(
                section.items
                    .map((item) => {
                        const line = item.label ? `**${item.label}:** ${item.text}` : item.text;
                        const children = item.children?.map((child) => `  - ${child}`).join('\n');
                        return children ? `- ${line}\n${children}` : `- ${line}`;
                    })
                    .join('\n')
            );
        }
        parts.push(sectionParts.filter(Boolean).join('\n\n'));
    }

    return parts.join('\n\n') + '\n';
}
