import { describe, expect, it } from 'vitest';

import { telegramManagedBotSetupPollInterval } from './use-pod-surfaces';

describe('telegram managed-bot setup polling', () => {
    it('polls while the setup is pending', () => {
        expect(
            telegramManagedBotSetupPollInterval(
                'WAITING_FOR_TELEGRAM',
                'success',
            ),
        ).toBe(1500);
    });

    it.each(['COMPLETE', 'FAILED'])(
        'stops for terminal status %s',
        (status) => {
            expect(
                telegramManagedBotSetupPollInterval(status, 'success'),
            ).toBe(false);
        },
    );

    it('stops after the setup query errors', () => {
        expect(
            telegramManagedBotSetupPollInterval(
                'WAITING_FOR_TELEGRAM',
                'error',
            ),
        ).toBe(false);
    });
});
