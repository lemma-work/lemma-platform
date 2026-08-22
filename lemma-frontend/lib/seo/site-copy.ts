/**
 * The site's one thesis, in one place. Both the HTML `<head>` (title, Open
 * Graph, Twitter card in app/page.tsx) and the markdown variant of `/`
 * (lib/markdown/homepage.ts) read from here, so a crawler that gets the
 * markdown representation and one that reads the meta tags are told the same
 * thing.
 */
export const SITE_TITLE = "The software you need doesn't exist yet.";
export const SITE_DESCRIPTION =
    'Your coding agent can write it. Lemma turns it into something your team can actually use — and run anywhere.';
