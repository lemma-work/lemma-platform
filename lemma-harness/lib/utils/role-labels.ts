export function formatRoleLabel(role: string): string {
    return role
        .replace(/^(ORG|POD)_/, '')
        .toLowerCase()
        .split('_')
        .filter(Boolean)
        .map(part => part.charAt(0).toUpperCase() + part.slice(1))
        .join(' ');
}
