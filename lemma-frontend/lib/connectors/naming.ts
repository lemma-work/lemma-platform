/**
 * Picking the name for a connector install about to be created.
 *
 * An organization may hold many installs of one connector — two Slack apps,
 * several MCP servers — and they are told apart by name. Names are unique per
 * org among active installs, and a blank one falls back to the connector id,
 * so the first install takes `slack` and every one after it collided with it:
 * a database constraint surfacing as a failed save, in a dialog whose name
 * field was optional and left empty.
 *
 * Suggesting a free name is what makes that field's optionality true. Returns
 * `''` when the bare id is still available, because that is what the backend
 * would have chosen anyway — the same shape as `deriveSurfaceName` returning
 * `undefined` for the first surface of a platform.
 */
export function suggestInstallName(
    connectorId: string | undefined,
    existingNames: string[],
): string {
    const base = String(connectorId ?? '').toLowerCase();
    if (!base) return '';
    const taken = new Set(existingNames.map((name) => name.toLowerCase()));
    if (!taken.has(base)) return '';
    for (let n = 2; n < 100; n += 1) {
        if (!taken.has(`${base}-${n}`)) return `${base}-${n}`;
    }
    return `${base}-${taken.size + 1}`;
}
