/** Which plugins to offer somebody for what they said they do.
 *
 *  Each built plugin names the jobs it is for, in the server's ids (`Job`),
 *  the same ones the question saves. The ones matching most of the person's
 *  jobs come first, then the catalog's own order, which puts the plugins
 *  others build on ahead of the ones built on them. Somebody who skipped, or
 *  answered only in their own words, is offered the first few in that order:
 *  the question was optional, and the step should still have something to
 *  add. */

/** What the ranking reads off a plugin. */
export interface Suggestible {
    slug: string;
    position: number;
    jobs: readonly string[];
    install: string | null;
}

/** How many the step offers; the rest are a click away in the catalog. */
export const SUGGESTED = 3;

/** Every plugin that can be added from here, the ones fitting most of the
 *  person's jobs first, then the catalog's own order. */
export function rankPlugins<P extends Suggestible>(jobs: readonly string[], built: readonly P[]): P[] {
    const wanted = new Set(jobs);
    const matches = (plugin: P) => plugin.jobs.filter((job) => wanted.has(job)).length;
    return built.filter((plugin) => plugin.install).sort((a, b) => matches(b) - matches(a) || a.position - b.position);
}

export function suggestPlugins<P extends Suggestible>(jobs: readonly string[], built: readonly P[], count = SUGGESTED): P[] {
    const ranked = rankPlugins(jobs, built);
    const fitting = ranked.filter((plugin) => plugin.jobs.some((job) => jobs.includes(job)));
    return (fitting.length > 0 ? fitting : ranked).slice(0, count);
}

/** What one job's tab lists: every plugin for that job, the ones that also
 *  fit the person's other jobs first. */
export function pluginsForJob<P extends Suggestible>(job: string, jobs: readonly string[], built: readonly P[]): P[] {
    return rankPlugins(jobs, built).filter((plugin) => plugin.jobs.includes(job));
}

/** The picks that go in: every one still listed under a job they said they
 *  do, in the catalog's order (which puts the plugins others build on
 *  first). A pick survives moving between jobs; one whose every job was
 *  turned off is no longer on screen, so it is left out -- and comes back
 *  ticked if the job does. With no jobs at all, every plugin is listed. */
export function picksThatCount<P extends Suggestible>(picked: ReadonlySet<string>, jobs: readonly string[], built: readonly P[]): P[] {
    return built
        .filter((plugin) => plugin.install && picked.has(plugin.slug))
        .filter((plugin) => jobs.length === 0 || plugin.jobs.some((job) => jobs.includes(job)))
        .sort((a, b) => a.position - b.position);
}

/** Where adding a plugin from the way in starts: its install page, with the
 *  Lemma the person is about to land in already chosen as where it goes.
 *  They can still pick another there before anything is added. */
export function addHref(plugin: Suggestible): string | null {
    if (!plugin.install) return null;
    return plugin.install + (plugin.install.includes("?") ? "&" : "?") + "destination=existing";
}
