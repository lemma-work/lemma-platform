import kits from './kits.json';

export interface KitDefinition {
    id: string;
    name: string;
    description: string;
    github: string;
}

export type KitInstallMode = 'install' | 'customize';

export const kitCatalog = kits as KitDefinition[];

export function getKitById(id: string | null | undefined) {
    return kitCatalog.find((kit) => kit.id === id) ?? null;
}

export function parseGitHubRepoUrl(url: string) {
    try {
        const parsed = new URL(url);
        if (parsed.hostname !== 'github.com') return null;
        const [owner, repo] = parsed.pathname.split('/').filter(Boolean);
        if (!owner || !repo) return null;
        return { owner, repo };
    } catch {
        return null;
    }
}

export function getGitHubRepoLabel(kit: KitDefinition) {
    const repo = parseGitHubRepoUrl(kit.github);
    return repo ? `${repo.owner}/${repo.repo}` : kit.github;
}

export function getReadmeRawCandidates(kit: KitDefinition) {
    const repo = parseGitHubRepoUrl(kit.github);
    if (!repo) return [];
    return ['main', 'master'].map((branch) => ({
        branch,
        url: `https://raw.githubusercontent.com/${repo.owner}/${repo.repo}/${branch}/README.md`,
    }));
}

export function getRawGitHubAssetUrl(kit: KitDefinition, branch: string, assetPath: string) {
    if (/^https?:\/\//i.test(assetPath)) return assetPath;
    const repo = parseGitHubRepoUrl(kit.github);
    if (!repo) return assetPath;
    const normalized = assetPath.replace(/^\.\//, '').replace(/^\/+/, '');
    return `https://raw.githubusercontent.com/${repo.owner}/${repo.repo}/${branch}/${normalized}`;
}

export function absolutizeReadmeAssetUrls(markdown: string, kit: KitDefinition, branch: string) {
    return markdown.replace(/(!\[[^\]]*]\()((?!https?:\/\/|#|mailto:)[^)]+)(\))/gi, (_match, prefix: string, assetPath: string, suffix: string) => {
        return `${prefix}${getRawGitHubAssetUrl(kit, branch, assetPath)}${suffix}`;
    });
}

export function extractReadmeSummary(markdown: string) {
    const title = markdown.match(/^#\s+(.+)$/m)?.[1]?.trim() || '';
    const image = markdown.match(/!\[[^\]]*]\(([^)]+)\)/)?.[1]?.trim() || '';
    const withoutTitle = markdown.replace(/^#\s+.+$/m, '').trim();
    const intro = withoutTitle
        .split(/\n{2,}/)
        .map((block) => block.trim())
        .find((block) => block && !block.startsWith('![') && !block.startsWith('##')) || '';

    return { title, intro, image };
}

export function buildKitAssistantInstructions(kit: KitDefinition, mode: KitInstallMode, podName?: string | null) {
    const installBehavior = mode === 'install'
        ? [
            'The user chose Install. Use the source README as the source of truth.',
            'Inspect the source install instructions and ask only blocking setup questions.',
            'If the source is a Lemma pod export, import or provision the resources into the current pod.',
        ].join('\n')
        : [
            'The user chose Customize first. Do not provision resources immediately.',
            'Use the source README as the source of truth, then ask what should change for this pod.',
            'Only create or import pod resources after the user explicitly approves the customized plan.',
        ].join('\n');

    return [
        `You are customizing and installing the ${kit.name} kit inside the ${podName || 'current'} pod.`,
        '',
        installBehavior,
        '',
        `Kit description: ${kit.description}`,
        `Source: ${kit.github}`,
        '',
        'Guardrails:',
        '- Treat the source README as the install plan.',
        '- Keep the pod bounded to this one operating loop.',
        '- Reuse matching existing pod resources instead of creating duplicates.',
        '- Design first, provision second. For Customize first, approval is required before provisioning.',
        '- Preserve explicit human approval for public or external actions unless the user deliberately changes that.',
    ].join('\n');
}

export function buildKitAssistantOpeningMessage(kit: KitDefinition, mode: KitInstallMode) {
    if (mode === 'install') {
        return [
            `Install the ${kit.name} kit in this pod.`,
            '',
            `Use this source as the source of truth: ${kit.github}`,
            'Read the README, ask only for setup details that block installation, then install or import the kit into this pod.',
        ].join('\n');
    }

    return [
        `Customize the ${kit.name} kit before installing it.`,
        '',
            `Use this source as the source of truth: ${kit.github}`,
        'Start by reading the README, summarize the draft install plan, ask what should be different, and wait for approval before creating resources.',
    ].join('\n');
}
