// The legal entity behind Lemma. "Lemma" is the product; this is the company
// that owns it, contracts with customers, and holds the marks. Incorporated in
// Delaware on July 29, 2026 (file number 10715678).
//
// Everything user-facing that has to name the company — footers, the privacy
// policy, the terms, page metadata — reads from here so the name is spelled one
// way in one place.

// Ends in a period. When it lands at the end of a sentence, let the "Inc." do
// the terminating — writing `${COMPANY_LEGAL_NAME}.` renders "Inc..".
export const COMPANY_LEGAL_NAME = "Folks and Machines, Inc.";

/** For prose where the corporate suffix reads as noise. */
export const COMPANY_SHORT_NAME = "Folks and Machines";

/** How the entity is described the first time a legal document names it. */
export const COMPANY_DESCRIPTION = `${COMPANY_LEGAL_NAME}, a Delaware corporation`;

/** "© 2026 Folks and Machines, Inc." — the year defaults to the current one. */
export function copyrightNotice(year: number = new Date().getFullYear()): string {
  return `© ${year} ${COMPANY_LEGAL_NAME}`;
}
