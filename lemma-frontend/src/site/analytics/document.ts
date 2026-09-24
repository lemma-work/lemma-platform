/** The containing page owns analytics and consent for embedded demos. */
export function isAnalyticsDocument(): boolean {
    return typeof window !== "undefined" && window.self === window.top;
}
