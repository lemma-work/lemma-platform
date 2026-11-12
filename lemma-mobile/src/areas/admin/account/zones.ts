/** Time zones to pick from, the phone's own first. Pure, so `tests/` can
 *  hold it.
 *
 *  The web asks the browser for every zone it knows (`Intl.supportedValuesOf`)
 *  and keeps a short list for one that cannot say. A phone's JavaScript
 *  engine may not enumerate them either, and eight zones is not a list
 *  anybody outside them can use, so the fallback here is the full set of
 *  zones people live in, by region. Whatever the server holds, and wherever
 *  the phone is, is always on the list, so opening the screen never changes
 *  a setting by failing to show it. */

const ZONES: readonly string[] = [
    "UTC",
    "Africa/Abidjan", "Africa/Accra", "Africa/Addis_Ababa", "Africa/Algiers", "Africa/Cairo", "Africa/Casablanca", "Africa/Dar_es_Salaam",
    "Africa/Johannesburg", "Africa/Kampala", "Africa/Khartoum", "Africa/Kinshasa", "Africa/Lagos", "Africa/Luanda", "Africa/Maputo",
    "Africa/Nairobi", "Africa/Tripoli", "Africa/Tunis", "Africa/Windhoek",
    "America/Adak", "America/Anchorage", "America/Argentina/Buenos_Aires", "America/Asuncion", "America/Bogota", "America/Caracas",
    "America/Chicago", "America/Costa_Rica", "America/Denver", "America/Edmonton", "America/El_Salvador", "America/Guatemala",
    "America/Guayaquil", "America/Halifax", "America/Havana", "America/Jamaica", "America/La_Paz", "America/Lima", "America/Los_Angeles",
    "America/Managua", "America/Manaus", "America/Mexico_City", "America/Monterrey", "America/Montevideo", "America/New_York",
    "America/Panama", "America/Phoenix", "America/Port-au-Prince", "America/Puerto_Rico", "America/Regina", "America/Santiago",
    "America/Santo_Domingo", "America/Sao_Paulo", "America/St_Johns", "America/Tegucigalpa", "America/Tijuana", "America/Toronto",
    "America/Vancouver", "America/Winnipeg",
    "Asia/Almaty", "Asia/Amman", "Asia/Baghdad", "Asia/Baku", "Asia/Bangkok", "Asia/Beirut", "Asia/Colombo", "Asia/Damascus",
    "Asia/Dhaka", "Asia/Dubai", "Asia/Ho_Chi_Minh", "Asia/Hong_Kong", "Asia/Jakarta", "Asia/Jerusalem", "Asia/Kabul", "Asia/Karachi",
    "Asia/Kathmandu", "Asia/Kolkata", "Asia/Kuala_Lumpur", "Asia/Kuwait", "Asia/Manila", "Asia/Muscat", "Asia/Qatar", "Asia/Riyadh",
    "Asia/Seoul", "Asia/Shanghai", "Asia/Singapore", "Asia/Taipei", "Asia/Tashkent", "Asia/Tbilisi", "Asia/Tehran", "Asia/Tokyo",
    "Asia/Ulaanbaatar", "Asia/Vladivostok", "Asia/Yangon", "Asia/Yekaterinburg", "Asia/Yerevan",
    "Atlantic/Azores", "Atlantic/Canary", "Atlantic/Cape_Verde", "Atlantic/Reykjavik",
    "Australia/Adelaide", "Australia/Brisbane", "Australia/Darwin", "Australia/Hobart", "Australia/Melbourne", "Australia/Perth", "Australia/Sydney",
    "Europe/Amsterdam", "Europe/Athens", "Europe/Belgrade", "Europe/Berlin", "Europe/Brussels", "Europe/Bucharest", "Europe/Budapest",
    "Europe/Copenhagen", "Europe/Dublin", "Europe/Helsinki", "Europe/Istanbul", "Europe/Kyiv", "Europe/Lisbon", "Europe/London",
    "Europe/Madrid", "Europe/Moscow", "Europe/Oslo", "Europe/Paris", "Europe/Prague", "Europe/Riga", "Europe/Rome", "Europe/Sofia",
    "Europe/Stockholm", "Europe/Tallinn", "Europe/Vienna", "Europe/Vilnius", "Europe/Warsaw", "Europe/Zurich",
    "Indian/Maldives", "Indian/Mauritius",
    "Pacific/Auckland", "Pacific/Fiji", "Pacific/Guam", "Pacific/Honolulu", "Pacific/Port_Moresby", "Pacific/Tongatapu",
];

/** Every zone worth offering: the engine's own list when it has one, else
 *  the list above; the phone's zone first, then whatever is held, then the
 *  rest in order. */
export function zoneChoices(here: string, held: string, supported?: readonly string[] | null): string[] {
    const all = supported && supported.length ? [...supported] : [...ZONES];
    const front = [here, held].filter((zone, index, both) => zone && both.findIndex((other) => sameZone(other, zone)) === index);
    return [...front, ...all.filter((zone) => !front.some((one) => sameZone(one, zone)))];
}

/** Zones renamed in the time zone database, old name to new. A phone says
 *  "Asia/Kolkata" where an account set years ago holds "Asia/Calcutta", and
 *  the two are the same clock: offering to move between them is noise. */
const RENAMED: Record<string, string> = {
    "Asia/Calcutta": "Asia/Kolkata",
    "Asia/Saigon": "Asia/Ho_Chi_Minh",
    "Asia/Katmandu": "Asia/Kathmandu",
    "Asia/Rangoon": "Asia/Yangon",
    "Asia/Dacca": "Asia/Dhaka",
    "Asia/Thimbu": "Asia/Thimphu",
    "Asia/Ulan_Bator": "Asia/Ulaanbaatar",
    "Asia/Macao": "Asia/Macau",
    "Europe/Kiev": "Europe/Kyiv",
    "America/Godthab": "America/Nuuk",
    "America/Buenos_Aires": "America/Argentina/Buenos_Aires",
    "Atlantic/Faeroe": "Atlantic/Faroe",
    "Pacific/Truk": "Pacific/Chuuk",
    "Pacific/Ponape": "Pacific/Pohnpei",
};

/** Whether two zone names are the same clock, old names and new. */
export function sameZone(a: string, b: string): boolean {
    return (RENAMED[a] ?? a) === (RENAMED[b] ?? b);
}

/** The engine's own zones, when it can list them. */
export function supportedZones(): string[] | null {
    try {
        const list = (Intl as unknown as { supportedValuesOf?: (key: string) => string[] }).supportedValuesOf;
        return typeof list === "function" ? list("timeZone") : null;
    } catch {
        return null;
    }
}

/** "Asia/Kolkata" → "Kolkata"; "America/Argentina/Buenos_Aires" → "Buenos Aires". */
export function zoneCity(zone: string): string {
    const last = zone.split("/").pop() ?? zone;
    return last.replace(/_/g, " ");
}

/** "Asia/Kolkata" → "Asia"; "UTC" → "". */
export function zoneRegion(zone: string): string {
    const parts = zone.split("/");
    return parts.length > 1 ? parts.slice(0, -1).join(" · ").replace(/_/g, " ") : "";
}

/** Whether a zone answers what was typed: its city, its region, or the
 *  whole name, ignoring case, spaces and underscores. */
export function zoneMatches(zone: string, query: string): boolean {
    const wanted = query.trim().toLowerCase().replace(/[\s_]+/g, " ");
    if (!wanted) return true;
    return zone.toLowerCase().replace(/[_/]+/g, " ").includes(wanted);
}

/** A date as the profile stores it: `1990-04-23`, in the phone's own day. */
export function isoDay(date: Date): string {
    const month = String(date.getMonth() + 1).padStart(2, "0");
    const day = String(date.getDate()).padStart(2, "0");
    return `${date.getFullYear()}-${month}-${day}`;
}

/** `1990-04-23` → "23 April 1990"; "" for nothing or something else. */
export function spokenDay(value: string): string {
    const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value.trim());
    if (!match) return "";
    const months = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
    const month = months[Number(match[2]) - 1];
    return month ? `${Number(match[3])} ${month} ${match[1]}` : "";
}

/** The stored day as a date to start a picker on, at midday so no zone
 *  moves it to the day before. */
export function dayToDate(value: string): Date | null {
    const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value.trim());
    if (!match) return null;
    const date = new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]), 12);
    return Number.isNaN(date.getTime()) ? null : date;
}
