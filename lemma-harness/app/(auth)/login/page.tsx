import { redirect } from 'next/navigation';
import { buildAuthUrl, resolveSafeRedirectUri } from 'lemma-sdk';
import { getLemmaAuthUrl } from '@/lib/sdk/lemma-client';
import { config } from '@/lib/config';

type SearchParamMap = {
    [key: string]: string | string[] | undefined;
};

function getSiteOrigin(): string {
    try {
        return new URL(config.SITE_URL).origin;
    } catch {
        return 'http://localhost:3000';
    }
}

function readRedirectUri(searchParams: SearchParamMap): string | undefined {
    const value = searchParams.redirect_uri ?? searchParams.redirectTo ?? searchParams.next;
    return Array.isArray(value) ? value[0] : value;
}

export default async function LoginPage({
    searchParams,
}: {
    searchParams: Promise<SearchParamMap>;
}) {
    const resolvedSearchParams = await searchParams;
    const redirectUri = resolveSafeRedirectUri(readRedirectUri(resolvedSearchParams), {
        siteOrigin: getSiteOrigin(),
        fallback: '/',
        allowedOriginSuffixes: config.APPS_DOMAIN_SUFFIX ? [config.APPS_DOMAIN_SUFFIX] : undefined,
    });

    redirect(buildAuthUrl(getLemmaAuthUrl(), { redirectUri }));
}
