export const POD_SHARE_CARD_WIDTH = 1200;
export const POD_SHARE_CARD_HEIGHT = 630;

export interface PodShareCardInput {
    podName?: string | null;
    repoUrl?: string | null;
}

function escapeXml(value: string): string {
    return value
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&apos;');
}

function normalizedPodName(value?: string | null): string {
    const name = value?.replace(/\s+/g, ' ').trim();
    return name || 'A Lemma pod';
}

function compactRepoLabel(value?: string | null): string {
    if (!value) return 'lemma.work';
    try {
        const url = new URL(value);
        const path = url.pathname.replace(/^\/|\/$/g, '');
        return [url.hostname.replace(/^www\./, ''), path].filter(Boolean).join('/');
    } catch {
        return value.replace(/^https?:\/\//, '').replace(/\/$/, '');
    }
}
export function splitPodShareCardTitle(value?: string | null): string[] {
    const source = normalizedPodName(value);
    const maxLineLength = 24;
    const maxTotalLength = 52;
    const shortened =
        source.length > maxTotalLength
            ? `${source.slice(0, maxTotalLength - 1).trimEnd()}…`
            : source;

    if (shortened.length <= maxLineLength) return [shortened];

    const words = shortened.split(' ');
    let first = '';
    let second = '';

    for (const word of words) {
        if (!first || `${first} ${word}`.length <= maxLineLength) {
            first = first ? `${first} ${word}` : word;
        } else {
            second = second ? `${second} ${word}` : word;
        }
    }

    if (!second) {
        return [
            shortened.slice(0, maxLineLength),
            shortened.slice(maxLineLength),
        ];
    }

    return [first, second];
}

export function buildPodShareCardCopy(input: PodShareCardInput): string {
    const name = normalizedPodName(input.podName);
    const url = input.repoUrl?.trim();
    return [`Run ${name} on Lemma.`, url].filter(Boolean).join('\n\n');
}

export function buildPodShareCardSvg(input: PodShareCardInput): string {
    const titleLines = splitPodShareCardTitle(input.podName).map(escapeXml);
    const repoLabel = escapeXml(compactRepoLabel(input.repoUrl));
    const titleFontSize =
        Math.max(...titleLines.map((line) => line.length)) > 22 ? 68 : 78;
    const secondLine = titleLines[1]
        ? `<tspan x="64" dy="${titleFontSize + 10}">${titleLines[1]}</tspan>`
        : '';

    return `<svg xmlns="http://www.w3.org/2000/svg" width="${POD_SHARE_CARD_WIDTH}" height="${POD_SHARE_CARD_HEIGHT}" viewBox="0 0 ${POD_SHARE_CARD_WIDTH} ${POD_SHARE_CARD_HEIGHT}">
  <rect width="1200" height="630" fill="#F3F1EA"/>
  <path d="M790 0H1200V630H790Z" fill="#E9E6DC"/>
  <path d="M790 0H1200V630H790Z" fill="url(#grain)" opacity=".22"/>
  <defs>
    <pattern id="grain" width="32" height="32" patternUnits="userSpaceOnUse">
      <circle cx="1" cy="1" r="1" fill="#11110F" opacity=".13"/>
    </pattern>
  </defs>

  <g transform="translate(64 56)">
    <rect x="0" y="20" width="10" height="14" rx="2" fill="#11110F"/>
    <rect x="15" y="10" width="10" height="24" rx="2" fill="#11110F"/>
    <rect x="30" y="0" width="10" height="34" rx="2" fill="#11110F"/>
    <text x="56" y="29" fill="#11110F" font-family="Arial, Helvetica, sans-serif" font-size="27" font-weight="700" letter-spacing="-.8">Lemma</text>
  </g>

  <text x="64" y="186" fill="#595851" font-family="ui-monospace, SFMono-Regular, Menlo, monospace" font-size="19" font-weight="600" letter-spacing="3.2">RUN IT ON LEMMA</text>
  <text x="64" y="286" fill="#11110F" font-family="Arial, Helvetica, sans-serif" font-size="${titleFontSize}" font-weight="700" letter-spacing="-3.2">
    <tspan x="64">${titleLines[0]}</tspan>${secondLine}
  </text>

  <g transform="translate(846 96)">
    <rect x="0" y="0" width="278" height="438" rx="28" fill="#F8F7F2" stroke="#C9C5BA" stroke-width="2"/>
    <rect x="32" y="34" width="112" height="14" rx="7" fill="#11110F"/>
    <rect x="32" y="60" width="174" height="8" rx="4" fill="#BBB7AC"/>
    <rect x="32" y="106" width="214" height="84" rx="16" fill="#E8F0D9" stroke="#B7C49D"/>
    <circle cx="60" cy="134" r="10" fill="#66833E"/>
    <rect x="80" y="127" width="126" height="10" rx="5" fill="#587137"/>
    <rect x="80" y="147" width="92" height="7" rx="3.5" fill="#98AB78"/>
    <rect x="32" y="214" width="214" height="84" rx="16" fill="#E4EBF2" stroke="#B3C1D0"/>
    <circle cx="60" cy="242" r="10" fill="#4A6580"/>
    <rect x="80" y="235" width="112" height="10" rx="5" fill="#405B74"/>
    <rect x="80" y="255" width="142" height="7" rx="3.5" fill="#8EA2B5"/>
    <path d="M139 190V214M139 298V326" stroke="#89867D" stroke-width="2" stroke-dasharray="5 5"/>
    <rect x="32" y="326" width="214" height="74" rx="16" fill="#11110F"/>
    <rect x="56" y="351" width="104" height="10" rx="5" fill="#F8F7F2"/>
    <rect x="56" y="371" width="136" height="7" rx="3.5" fill="#8A8983"/>
    <circle cx="218" cy="363" r="13" fill="#F3F1EA"/>
    <path d="M213 363H223M219 359L223 363L219 367" stroke="#11110F" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
  </g>

  <path d="M64 526H742" stroke="#C5C1B6" stroke-width="2"/>
  <text x="64" y="562" fill="#595851" font-family="Arial, Helvetica, sans-serif" font-size="18">Apps · agents · workflows · data</text>
  <text x="64" y="594" fill="#11110F" font-family="ui-monospace, SFMono-Regular, Menlo, monospace" font-size="15">${repoLabel}</text>
</svg>`;
}

export function podShareCardDataUrl(input: PodShareCardInput): string {
    return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(buildPodShareCardSvg(input))}`;
}

export function podShareCardFilename(value?: string | null): string {
    const slug = normalizedPodName(value)
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, '-')
        .replace(/^-+|-+$/g, '')
        .slice(0, 64);
    return `${slug || 'lemma-pod'}-share-card.png`;
}

export async function renderPodShareCardPng(input: PodShareCardInput): Promise<Blob> {
    const svgBlob = new Blob([buildPodShareCardSvg(input)], {
        type: 'image/svg+xml;charset=utf-8',
    });
    const sourceUrl = URL.createObjectURL(svgBlob);

    try {
        const image = new Image();
        image.decoding = 'async';
        image.src = sourceUrl;
        await image.decode();

        const canvas = document.createElement('canvas');
        canvas.width = POD_SHARE_CARD_WIDTH;
        canvas.height = POD_SHARE_CARD_HEIGHT;
        const context = canvas.getContext('2d');
        if (!context) throw new Error('Image rendering is unavailable in this browser.');
        context.drawImage(image, 0, 0, POD_SHARE_CARD_WIDTH, POD_SHARE_CARD_HEIGHT);

        return await new Promise<Blob>((resolve, reject) => {
            canvas.toBlob(
                (blob) => {
                    if (blob) resolve(blob);
                    else reject(new Error('Could not create the share-card image.'));
                },
                'image/png',
                1,
            );
        });
    } finally {
        URL.revokeObjectURL(sourceUrl);
    }
}
