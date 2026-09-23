import { redirect } from 'next/navigation';

type SearchParamMap = {
    [key: string]: string | string[] | undefined;
};

function toSearchParams(searchParams: SearchParamMap): URLSearchParams {
    const params = new URLSearchParams();

    for (const [key, value] of Object.entries(searchParams)) {
        if (value === undefined) continue;

        if (Array.isArray(value)) {
            for (const item of value) {
                params.append(key, item);
            }
            continue;
        }

        params.set(key, value);
    }

    return params;
}

export default async function LegacyLandingPage({
    searchParams,
}: {
    searchParams: Promise<SearchParamMap>;
}) {
    const params = toSearchParams(await searchParams);
    const queryString = params.toString();

    redirect(queryString ? `/?${queryString}` : '/');
}
