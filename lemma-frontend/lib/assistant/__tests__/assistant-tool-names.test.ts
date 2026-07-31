import { describe, expect, it } from 'vitest';

import { normalizeToolNameForDisplay } from '@/components/lemma/assistant/assistant-format';
import { toolIconKind } from '@/components/lemma/assistant/assistant-tool-icon';

describe('normalizeToolNameForDisplay', () => {
    it('uses canonical Lemma MCP names for contextual tool rendering', () => {
        expect(normalizeToolNameForDisplay('mcp__lemma_tools__lemma_exec_command')).toBe('exec_command');
        expect(normalizeToolNameForDisplay('lemma_tools_lemma_display_resource')).toBe('display_resource');
        expect(normalizeToolNameForDisplay('commandExecution')).toBe('command_execution');
    });
});

describe('toolIconKind', () => {
    it('assigns small semantic icons from canonical and wrapped tool names', () => {
        expect(toolIconKind('mcp__lemma_tools__lemma_exec_command')).toBe('terminal');
        expect(toolIconKind('display_resource')).toBe('display');
        expect(toolIconKind('ask_user')).toBe('question');
        expect(toolIconKind('pod_write_record')).toBe('data');
        expect(toolIconKind('spawn_subagent')).toBe('agent');
        expect(toolIconKind('unknown_custom_tool')).toBe('tool');
    });
});
