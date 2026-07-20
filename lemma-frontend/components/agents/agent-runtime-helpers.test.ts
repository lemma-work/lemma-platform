import { describe, expect, it } from 'vitest';
import type { AgentHarnessInfo } from 'lemma-sdk';

import {
    availableHarnessKey,
    availableHarnessStatusLabel,
    firstHarnessModelName,
    isHarnessAvailable,
    runtimeAvailabilityLabel,
    runtimeProfileDaemonKey,
} from './agent-runtime-helpers';

const base = {
    harness_kind: 'GG_CODER',
    display_name: 'GG Coder',
    models: ['default'],
    model_catalog: [],
} as const;

describe('isHarnessAvailable', () => {
    it('returns true when the daemon is ONLINE and the binary is installed', () => {
        expect(
            isHarnessAvailable({
                ...base,
                available: true,
                availability_status: 'READY',
                daemon_status: 'ONLINE',
            } as unknown as AgentHarnessInfo),
        ).toBe(true);
    });

    it('returns false when availability_status is NOT_INSTALLED', () => {
        expect(
            isHarnessAvailable({
                ...base,
                available: false,
                availability_status: 'NOT_INSTALLED',
                daemon_status: 'ONLINE',
            } as unknown as AgentHarnessInfo),
        ).toBe(false);
    });

    it('returns false when the daemon is OFFLINE even if available was true', () => {
        // This is the darrenschreuder@webrnds.com symptom: the daemon's last
        // catalog said GG_CODER was installed but the daemon hasn't been online
        // for hours, so we MUST refuse to show the Add button.
        expect(
            isHarnessAvailable({
                ...base,
                available: false,
                availability_status: 'DAEMON_OFFLINE',
                daemon_status: 'OFFLINE',
            } as unknown as AgentHarnessInfo),
        ).toBe(false);
    });
});

describe('availableHarnessStatusLabel', () => {
    it('returns "Not installed" when the binary is missing on PATH', () => {
        expect(
            availableHarnessStatusLabel({
                ...base,
                available: false,
                availability_status: 'NOT_INSTALLED',
                daemon_status: 'ONLINE',
            } as unknown as AgentHarnessInfo),
        ).toBe('Not installed');
    });

    it('returns "Daemon offline" when the binary is installed but the daemon is OFFLINE', () => {
        // Surfaces the right hint to the operator so they don't confuse
        // "binary missing" with "daemon not running".
        expect(
            availableHarnessStatusLabel({
                ...base,
                available: false,
                availability_status: 'DAEMON_OFFLINE',
                daemon_status: 'OFFLINE',
            } as unknown as AgentHarnessInfo),
        ).toBe('Daemon offline');
    });

    it('returns null when the daemon is ONLINE and the binary is installed', () => {
        // No badge on the row; the Add button is the action.
        expect(
            availableHarnessStatusLabel({
                ...base,
                available: true,
                availability_status: 'READY',
                daemon_status: 'ONLINE',
            } as unknown as AgentHarnessInfo),
        ).toBe(null);
    });

    it('falls back to "Daemon offline" when daemon_status is missing but availability_status is DAEMON_OFFLINE', () => {
        expect(
            availableHarnessStatusLabel({
                ...base,
                available: false,
                availability_status: 'DAEMON_OFFLINE',
                daemon_status: undefined,
            } as unknown as AgentHarnessInfo),
        ).toBe('Daemon offline');
    });
});

describe('runtimeAvailabilityLabel + runtimeProfileDaemonKey + firstHarnessModelName + availableHarnessKey', () => {
    it('runtimeAvailabilityLabel maps DAEMON_OFFLINE to "Offline" for saved profiles', () => {
        expect(
            runtimeAvailabilityLabel({
                daemon_id: '00000000-0000-0000-0000-000000000001',
                daemon_status: 'OFFLINE',
                availability_status: 'DAEMON_OFFLINE',
            } as unknown as Parameters<typeof runtimeAvailabilityLabel>[0]),
        ).toBe('Offline');
    });

    it('runtimeProfileDaemonKey formats the daemon+kind key used to reconcile saved profiles with detected harnesses', () => {
        expect(
            runtimeProfileDaemonKey({
                daemon_id: '00000000-0000-0000-0000-000000000001',
                derived_harness_kind: 'GG_CODER',
            } as unknown as Parameters<typeof runtimeProfileDaemonKey>[0]),
        ).toBe('00000000-0000-0000-0000-000000000001::GG_CODER');
    });

    it('firstHarnessModelName reads the first string model', () => {
        expect(
            firstHarnessModelName({
                models: ['ggcoder/default', 'ggcoder/pro'],
            } as unknown as Parameters<typeof firstHarnessModelName>[0]),
        ).toBe('ggcoder/default');
    });

    it('availableHarnessKey falls back to "daemonless" when daemon_id is missing', () => {
        expect(
            availableHarnessKey({
                daemon_id: undefined,
                harness_kind: 'GG_CODER',
            } as unknown as Parameters<typeof availableHarnessKey>[0]),
        ).toBe('daemonless::GG_CODER');
    });
});
