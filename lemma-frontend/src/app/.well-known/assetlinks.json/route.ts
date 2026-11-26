import { assetLinks } from '@/site/app-links';

export const dynamic = 'force-static';

export function GET(): Response {
    return Response.json(assetLinks());
}
