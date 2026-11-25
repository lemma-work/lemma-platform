/* app.json, with the servers a build names in EXPO_PUBLIC_LEMMA_SERVERS
   (a git-ignored .env.local; README, "Which server") added to the web links
   that open the app, beside lemma.work's, on Android and iOS. */

function namedHosts() {
    try {
        const list = JSON.parse(process.env.EXPO_PUBLIC_LEMMA_SERVERS || "[]");
        return (Array.isArray(list) ? list : []).flatMap((one) => {
            try {
                return [new URL(one.web).host];
            } catch {
                return [];
            }
        });
    } catch {
        return [];
    }
}

module.exports = ({ config }) => {
    const hosts = namedHosts();
    if (hosts.length === 0) return config;
    const intentFilters = (config.android?.intentFilters ?? []).map((filter) => {
        const ours = (filter.data ?? []).filter((one) => one.host === "lemma.work");
        return ours.length ? { ...filter, data: [...filter.data, ...hosts.flatMap((host) => ours.map((one) => ({ ...one, host })))] } : filter;
    });
    /* iOS opens a named server's links only if that server serves the
       app's /.well-known/apple-app-site-association too. */
    const associatedDomains = [...(config.ios?.associatedDomains ?? []), ...hosts.map((host) => "applinks:" + host)];
    return { ...config, ios: { ...config.ios, associatedDomains }, android: { ...config.android, intentFilters } };
};
