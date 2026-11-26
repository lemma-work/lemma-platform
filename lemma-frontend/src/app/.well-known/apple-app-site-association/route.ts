import { appleAppSiteAssociation } from '@/site/app-links';

/* No extension, served as JSON, with no redirect: Apple's fetch refuses
   anything else. */
export const dynamic = 'force-static';

export function GET(): Response {
    return Response.json(appleAppSiteAssociation());
}
