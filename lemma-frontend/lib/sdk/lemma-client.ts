import { LemmaClient } from 'lemma-sdk';
import { config } from '@/lib/config';
import { desktopBridgeAvailable } from '@/lib/desktop/local-capabilities';

function toOrigin(value: string): string | null {
    try {
        return new URL(value).origin;
    } catch {
        return null;
    }
}

function getApiBaseUrl(): string {
    return config.API_URL;
}

function getAuthBaseUrl(): string {
    const authUrl = config.AUTH_URL;
    const withProtocol = /^https?:\/\//i.test(authUrl) ? authUrl : `https://${authUrl}`;
    const origin = toOrigin(withProtocol);
    return origin ? `${origin}/auth` : `${config.SITE_URL.replace(/\/$/, '')}/auth`;
}

let baseClient: LemmaClient | null = null;
const scopedClients = new Map<string, LemmaClient>();

function createBaseClient(): LemmaClient {
    return new LemmaClient({
        apiUrl: getApiBaseUrl(),
        authUrl: getAuthBaseUrl(),
        // Naming the client is what makes WEB and DESKTOP reachable as origins.
        // Unnamed, this SDK identifies as `lemma-sdk-ts` and every human's
        // traffic lands under SDK -- indistinguishable from somebody's script,
        // which makes "how much of this pod's work is a person?" unanswerable.
        client: desktopBridgeAvailable() ? 'lemma-desktop' : 'lemma-web',
    });
}

function getBaseClient(): LemmaClient {
    if (!baseClient) {
        baseClient = createBaseClient();
        scopedClients.set('__default__', baseClient);
    }
    return baseClient;
}

export function getLemmaClient(podId?: string): LemmaClient {
    if (!podId) {
        return getBaseClient();
    }

    const key = podId.trim();
    const cached = scopedClients.get(key);
    if (cached) return cached;

    const scopedClient = getBaseClient().withPod(key);
    scopedClients.set(key, scopedClient);
    return scopedClient;
}

export function getLemmaApiBaseUrl(): string {
    return getApiBaseUrl();
}

export function getLemmaAuthUrl(): string {
    return getAuthBaseUrl();
}

export async function getLemmaRequestHeaders(initial?: HeadersInit): Promise<HeadersInit> {
    return { ...(initial || {}) };
}
