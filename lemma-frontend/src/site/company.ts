export const COMPANY_LEGAL_NAME = "Folks and Machines, Inc.";

/** For prose where the corporate suffix reads as noise. */
export const COMPANY_SHORT_NAME = "Folks and Machines";

/** How the entity is described the first time a legal document names it. */
export const COMPANY_DESCRIPTION = `${COMPANY_LEGAL_NAME}, a Delaware corporation`;

/** "© 2026 Folks and Machines, Inc." — the year defaults to the current one. */
export function copyrightNotice(
    year: number = new Date().getFullYear(),
): string {
    return `© ${year} ${COMPANY_LEGAL_NAME}`;
}
