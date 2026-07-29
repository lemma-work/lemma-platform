/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AgentRuntimeConfig } from './AgentRuntimeConfig.js';
import type { AnthropicCompatibleRuntimeProfileResponse } from './AnthropicCompatibleRuntimeProfileResponse.js';
import type { AzureOpenAIRuntimeProfileResponse } from './AzureOpenAIRuntimeProfileResponse.js';
import type { GoogleVertexRuntimeProfileResponse } from './GoogleVertexRuntimeProfileResponse.js';
import type { HarnessRuntimeProfileResponse } from './HarnessRuntimeProfileResponse.js';
import type { OpenAICompatibleRuntimeProfileResponse } from './OpenAICompatibleRuntimeProfileResponse.js';
export type AgentRuntimeProfileListResponse = {
    default_runtime: AgentRuntimeConfig;
    items: Array<(OpenAICompatibleRuntimeProfileResponse | AnthropicCompatibleRuntimeProfileResponse | AzureOpenAIRuntimeProfileResponse | GoogleVertexRuntimeProfileResponse | HarnessRuntimeProfileResponse)>;
};
