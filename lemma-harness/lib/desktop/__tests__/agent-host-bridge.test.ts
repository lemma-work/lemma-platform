import { describe, expect, it } from 'vitest';
import { readStatus } from '../agent-host-bridge';

// The shell hands over whatever locald last reported. Everything here is about
// not inventing state when that payload is missing, partial, or not there yet:
// showing a machine as connected when it is not sends work nowhere.
describe('this computer status', () => {
    it('has no opinion before the shell has answered', () => {
        expect(readStatus(null)).toBeNull();
        expect(readStatus({})).toBeNull();
        expect(readStatus('offline')).toBeNull();
    });

    it('reads a connected host', () => {
        const status = readStatus({
            available: true,
            running: true,
            desired_running: true,
            paired: true,
            uptime_seconds: 400,
            targets: [{ host_id: 'host-1', connection_state: 'ONLINE', active_runs: 2 }],
        });

        expect(status?.running).toBe(true);
        expect(status?.paired).toBe(true);
        expect(status?.targets[0].host_id).toBe('host-1');
        expect(status?.uptime_seconds).toBe(400);
    });

    it('treats every missing flag as not-yet-true', () => {
        // A sidecar that has never run reports almost nothing, and defaulting
        // any of these to true would show a green machine that cannot work.
        const status = readStatus({ available: true });

        expect(status).not.toBeNull();
        expect(status?.running).toBe(false);
        expect(status?.paired).toBe(false);
        expect(status?.desired_running).toBe(false);
        expect(status?.targets).toEqual([]);
        expect(status?.uptime_seconds).toBeNull();
        expect(status?.last_error).toBeNull();
    });
});
