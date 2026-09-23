export function slugifyOrganizationName(value: string): string {
    return value
        .trim()
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, '-')
        .replace(/^-+|-+$/g, '')
        .slice(0, 64);
}

export function emailDomainFromAddress(email?: string | null): string {
    const [, domain = ''] = String(email || '').split('@');
    return domain.trim().toLowerCase();
}

const PUBLIC_EMAIL_PROVIDER_DOMAINS = new Set([
    'gmail.com',
    'googlemail.com',
    'yahoo.com',
    'ymail.com',
    'rocketmail.com',
    'hotmail.com',
    'outlook.com',
    'live.com',
    'msn.com',
    'icloud.com',
    'me.com',
    'mac.com',
    'aol.com',
    'proton.me',
    'protonmail.com',
    'pm.me',
    'zoho.com',
    'mail.com',
    'gmx.com',
    'gmx.net',
    'fastmail.com',
    'hey.com',
    'tutanota.com',
    'yandex.com',
]);

export function isPublicEmailProviderDomain(domain: string): boolean {
    return PUBLIC_EMAIL_PROVIDER_DOMAINS.has(normalizeEmailDomain(domain));
}

export function workDomainFromEmail(email?: string | null): string {
    const domain = emailDomainFromAddress(email);
    return domain && !isPublicEmailProviderDomain(domain) ? domain : '';
}

export function normalizeEmailDomain(value: string): string {
    return value
        .trim()
        .toLowerCase()
        .replace(/^@+/, '')
        .replace(/^https?:\/\//, '')
        .replace(/\/.*$/, '');
}
