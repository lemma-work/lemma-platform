/**
 * The site's one thesis, in one place. Both the HTML `<head>` (title, Open
 * Graph, Twitter card in app/page.tsx) and the markdown variant of `/`
 * (lib/markdown/homepage.ts) read from here, so a crawler that gets the
 * markdown representation and one that reads the meta tags are told the same
 * thing.
 */
export const SITE_TITLE = "Shared Apps and Agents.";
export const SITE_DESCRIPTION =
    'Your coding agent writes the whole system. Lemma runs it, and your team uses it at a URL or from the tools they already have open.';
