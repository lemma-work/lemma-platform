export const interviews = [
    { id:"N1", company:"Northstar", role:"Operations lead", date:"Sep 18", theme:"Setup effort", excerpt:"We need someone to walk us through the first file. The demo made sense, but mapping our export is where we stalled.", context:"Trial ended before the first validated import. Buyer asked for guided help.", included:true },
    { id:"B2", company:"Birch", role:"Founder", date:"Sep 17", theme:"Setup effort", excerpt:"We ran out of time mapping our export. It wasn’t clear which fields we could leave blank, so we put it aside.", context:"Small team, no dedicated implementation owner. Setup and ownership may overlap.", included:true },
    { id:"E3", company:"Elm", role:"Customer operations", date:"Sep 16", theme:"Setup effort", excerpt:"The import errors left us unsure what to fix. We expected the sample to tell us which rows needed attention.", context:"Customer had mixed date formats. No guided session was offered.", included:true },
    { id:"P4", company:"Pine", role:"Finance lead", date:"Sep 15", theme:"Budget", excerpt:"The workflow fits; this quarter’s budget doesn’t. We completed the trial and would revisit it when planning next quarter.", context:"Counterexample: onboarding succeeded. Budget blocked purchase.", included:true },
    { id:"W5", company:"Willow", role:"Team lead", date:"Sep 14", theme:"No owner", excerpt:"Everyone liked it, but nobody had time to own the rollout. We need to decide who will keep the report current.", context:"Cross-team ownership was unresolved. No specific import failure reported.", included:true },
];
export function evidenceCounts(sources: typeof interviews) {
    const included = sources.filter(source => source.included);
    return { total: included.length, themes: ["Setup effort","Budget","No owner"].map(theme => ({ theme, count: included.filter(source => source.theme===theme).length })) };
}
