export type AssetId = "landing" | "announcement" | "storyboard" | "story";
export type Copy = { title: string; body: string; cta: string };
export type Version = { number: number; copy: Copy; note: string };
export type Review = "Needs review" | "Changes requested" | "Approved";
export type Asset = { id: AssetId; name: string; format: string; owner: string; draft: Copy; versions: Version[]; review: Review; comments: { text: string; revision: number; author: string; resolved: boolean }[] };
export type Studio = { assets: Asset[]; date: string; anonymous: boolean; permission: boolean; shots: boolean[]; activity: string[] };
const definitions: [AssetId, string, string, string, Copy, Copy][] = [
    ["landing", "Landing page", "Web · Product launch", "Priya", { title: "Your first import.\nThe start of better work.", body: "Bring your customer data across without the spreadsheet back-and-forth. Map your columns, check a sample, and fix errors before they reach your team.", cta: "Try a guided import" }, { title: "All your customer data.\nFinally together.", body: "The all-in-one way to manage your customer imports and get your team up and running.", cta: "Get started" }],
    ["announcement", "Customer announcement", "Email · Existing customers", "Priya", { title: "A better way to bring your customers into Acme", body: "Hi there,\n\nYour first import should end with usable customer data, not a list of errors.\n\nStarting Thursday, Acme guides you through mapping columns, checking a sample, and fixing issues before the full import. Your original file stays unchanged.\n\nOpen Imports in your workspace to try it. If you get stuck, reply to this email and we’ll help with your first file.\n\nThe Acme team", cta: "Open Imports" }, { title: "Introducing our new import experience", body: "Hi there,\n\nWe’re excited to announce our new import experience. It’s faster and easier than ever.\n\nThe Acme team", cta: "Learn more" }],
    ["storyboard", "Product walkthrough", "Storyboard · 3 frames · 36 seconds", "Dev", { title: "From CSV to your first useful result", body: "Start with your existing file.\nMatch your columns and check a sample.\nFix the flagged rows, then bring everyone across.", cta: "Try it with your own file" }, { title: "Import walkthrough", body: "Upload your file.\nChoose your settings.\nExplore your workspace.", cta: "Get started" }],
    ["story", "Customer story", "Web · Customer proof", "Priya", { title: "A weekly import, without the weekly cleanup.", body: "Harbor’s operations team brings new customer records into Acme every week. Their first file had dates in two different formats.\n\nThe sample check surfaced the affected rows before the import. The team prepared a normalized ten-row sample for validation and kept their original export untouched. Validation is still pending.\n\nThis draft describes a fictional sample account. Customer attribution is awaiting review.", cta: "See how the import works" }, { title: "Harbor’s first import", body: "Harbor is preparing its first customer import. The team is checking date formats before uploading the full file.", cta: "Read the story" }],
];
export function initialStudio(): Studio {
    return { date: "2026-09-24", anonymous: false, permission: false, shots: [false, false, false], activity: ["Kit prepared the launch assets for review."], assets: definitions.map(([id, name, format, owner, copy, previous]) => ({ id, name, format, owner, draft: { ...copy }, versions: [{ number: 2, copy: { ...previous }, note: "Previous draft" }, { number: 3, copy: { ...copy }, note: "Kit · Updated from product review" }], review: "Needs review", comments: id === "landing" ? [{ text: "Keep the first import as the main promise. Don’t bring back ‘all-in-one’.", revision: 3, author: "Priya", resolved: false }] : [] })) };
}
export function isDirty(asset: Asset) { return JSON.stringify(asset.draft) !== JSON.stringify(asset.versions.at(-1)!.copy); }
export function blockers(studio: Studio, asset: Asset): string[] {
    return [
        ...(isDirty(asset) ? ["Save the current draft before reviewing it."] : []),
        ...(asset.id === "storyboard" && studio.shots.some(shot => !shot) ? ["Confirm all three storyboard frames have been updated."] : []),
        ...(asset.id === "story" && !studio.anonymous && !studio.permission ? ["Customer attribution needs permission or an anonymous version."] : []),
    ];
}
export function editAsset(studio: Studio, id: AssetId, patch: Partial<Copy>): Studio {
    return { ...studio, assets: studio.assets.map(asset => asset.id === id ? { ...asset, draft: { ...asset.draft, ...patch }, review: "Needs review" } : asset) };
}
export function saveAsset(studio: Studio, id: AssetId): Studio {
    const asset = studio.assets.find(item => item.id === id)!;
    if (!isDirty(asset)) return studio;
    const number = asset.versions.at(-1)!.number + 1;
    return { ...studio, activity: [`You saved ${asset.name} v${number}.`, ...studio.activity], assets: studio.assets.map(item => item.id === id ? { ...item, versions: [...item.versions, { number, copy: { ...item.draft }, note: "You · Saved revision" }], review: "Needs review" } : item) };
}
export function approveAsset(studio: Studio, id: AssetId): Studio {
    const asset = studio.assets.find(item => item.id === id)!;
    if (blockers(studio, asset).length) return studio;
    return { ...studio, activity: [`You approved ${asset.name} v${asset.versions.at(-1)!.number}.`, ...studio.activity], assets: studio.assets.map(item => item.id === id ? { ...item, review: "Approved" } : item) };
}
