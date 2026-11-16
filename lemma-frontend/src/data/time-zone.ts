/** The reader's time zone, as a name every server and database knows.
 *
 *  A browser answers with the name its own data calls canonical, and for a
 *  handful of zones that is the one the tz database renamed years ago: Chrome
 *  says `Asia/Calcutta` for India. A database built on a distribution that
 *  ships only current names refuses the old one ("time zone not
 *  recognized"), which reached people in those zones as a failed read. The
 *  list is the names CLDR still calls canonical where IANA has moved on; the
 *  server keeps the same one (`identity/domain/time_zones.py`). */
const RENAMED: Readonly<Record<string, string>> = {
    "Africa/Asmera": "Africa/Asmara",
    "America/Buenos_Aires": "America/Argentina/Buenos_Aires",
    "America/Catamarca": "America/Argentina/Catamarca",
    "America/Coral_Harbour": "America/Atikokan",
    "America/Cordoba": "America/Argentina/Cordoba",
    "America/Godthab": "America/Nuuk",
    "America/Indianapolis": "America/Indiana/Indianapolis",
    "America/Jujuy": "America/Argentina/Jujuy",
    "America/Louisville": "America/Kentucky/Louisville",
    "America/Mendoza": "America/Argentina/Mendoza",
    "Asia/Calcutta": "Asia/Kolkata",
    "Asia/Katmandu": "Asia/Kathmandu",
    "Asia/Rangoon": "Asia/Yangon",
    "Asia/Saigon": "Asia/Ho_Chi_Minh",
    "Atlantic/Faeroe": "Atlantic/Faroe",
    "Europe/Kiev": "Europe/Kyiv",
    "Pacific/Enderbury": "Pacific/Kanton",
    "Pacific/Ponape": "Pacific/Pohnpei",
    "Pacific/Truk": "Pacific/Chuuk",
};

/** A zone by its current name: a renamed one's new name, any other as it is. */
export function currentZoneName(name: string): string {
    return Object.hasOwn(RENAMED, name) ? RENAMED[name] : name;
}

/** The reader's own zone, so "today" is their today; UTC when the browser
 *  will not say. */
export function readerZone(): string {
    try {
        return currentZoneName(Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC");
    } catch {
        return "UTC";
    }
}
