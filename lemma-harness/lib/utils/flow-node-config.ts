import { NodeType } from '@/lib/types';

type NodeConfig = Record<string, unknown> | null | undefined;

function getStringValue(config: NodeConfig, key: string): string {
    const value = config?.[key];
    return typeof value === 'string' ? value : '';
}

export function getAgentNodeName(config: NodeConfig): string {
    return getStringValue(config, 'agent_name') || getStringValue(config, 'agent_id');
}

export function getFunctionNodeName(config: NodeConfig): string {
    return getStringValue(config, 'function_name') || getStringValue(config, 'function_id');
}

export function normalizeFlowNodeConfig(type: NodeType | string, rawConfig: NodeConfig): Record<string, unknown> {
    const config = rawConfig && typeof rawConfig === 'object' ? { ...rawConfig } : {};

    if (type === NodeType.AGENT) {
        const agentName = getAgentNodeName(config);
        delete config.agent_id;
        if (agentName) {
            config.agent_name = agentName;
        } else {
            delete config.agent_name;
        }
    }

    if (type === NodeType.FUNCTION) {
        const functionName = getFunctionNodeName(config);
        delete config.function_id;
        if (functionName) {
            config.function_name = functionName;
        } else {
            delete config.function_name;
        }
    }

    return config;
}

export function setAgentNodeName(config: NodeConfig, agentName: string): Record<string, unknown> {
    const nextConfig = normalizeFlowNodeConfig(NodeType.AGENT, config);
    if (agentName) {
        nextConfig.agent_name = agentName;
    } else {
        delete nextConfig.agent_name;
    }
    return nextConfig;
}

export function setFunctionNodeName(config: NodeConfig, functionName: string): Record<string, unknown> {
    const nextConfig = normalizeFlowNodeConfig(NodeType.FUNCTION, config);
    if (functionName) {
        nextConfig.function_name = functionName;
    } else {
        delete nextConfig.function_name;
    }
    return nextConfig;
}
