/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AgentRunStartResponse } from '../models/AgentRunStartResponse.js';
import type { ApprovalDecisionResponse } from '../models/ApprovalDecisionResponse.js';
import type { ConversationListResponse } from '../models/ConversationListResponse.js';
import type { ConversationResponse } from '../models/ConversationResponse.js';
import type { ConversationStatus } from '../models/ConversationStatus.js';
import type { ConversationType } from '../models/ConversationType.js';
import type { CreateConversationRequest } from '../models/CreateConversationRequest.js';
import type { MessageListResponse } from '../models/MessageListResponse.js';
import type { ResolveUserApprovalRequest } from '../models/ResolveUserApprovalRequest.js';
import type { SendMessageRequest } from '../models/SendMessageRequest.js';
import type { UpdateConversationRequest } from '../models/UpdateConversationRequest.js';
import type { UserApprovalListResponse } from '../models/UserApprovalListResponse.js';
import type { CancelablePromise } from '../core/CancelablePromise.js';
import { OpenAPI } from '../core/OpenAPI.js';
import { request as __request } from '../core/request.js';
export class AgentConversationsService {
    /**
     * List Pod Agent Conversations
     * List root conversations for the current user in a pod. Omit agent_name to list conversations across the pod, pass POD_DEFAULT (or pod_default) to list default pod assistant conversations, or pass a name to list conversations for a specific pod agent. Child (sub-agent) conversations are omitted by default; pass parent_id to list the children of a specific conversation instead.
     * @param podId
     * @param agentName
     * @param status
     * @param type
     * @param parentId
     * @param pageToken
     * @param limit
     * @returns ConversationListResponse Successful Response
     * @throws ApiError
     */
    public static agentConversationList(
        podId: string,
        agentName?: (string | null),
        status?: (ConversationStatus | null),
        type?: (ConversationType | null),
        parentId?: (string | null),
        pageToken?: (string | null),
        limit: number = 20,
    ): CancelablePromise<ConversationListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/conversations',
            path: {
                'pod_id': podId,
            },
            query: {
                'agent_name': agentName === null ? 'POD_DEFAULT' : agentName,
                'status': status,
                'type': type,
                'parent_id': parentId,
                'page_token': pageToken,
                'limit': limit,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Create Pod Agent Conversation
     * Create a new pod-scoped conversation. When agent_name is omitted, the conversation uses the default pod assistant. Workflow and sub-agent executions also use conversations as their external execution handle.
     * @param podId
     * @param requestBody
     * @returns ConversationResponse Successful Response
     * @throws ApiError
     */
    public static agentConversationCreate(
        podId: string,
        requestBody: CreateConversationRequest,
    ): CancelablePromise<ConversationResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/conversations',
            path: {
                'pod_id': podId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Pod Conversation
     * Get a single pod-scoped assistant or agent conversation by id.
     * @param podId
     * @param conversationId
     * @returns ConversationResponse Successful Response
     * @throws ApiError
     */
    public static agentConversationGet(
        podId: string,
        conversationId: string,
    ): CancelablePromise<ConversationResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/conversations/{conversation_id}',
            path: {
                'pod_id': podId,
                'conversation_id': conversationId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Update Pod Conversation
     * Update mutable conversation settings for a pod-scoped conversation. The conversation runtime is used by future runs; message sends do not carry per-request runtime overrides.
     * @param podId
     * @param conversationId
     * @param requestBody
     * @returns ConversationResponse Successful Response
     * @throws ApiError
     */
    public static agentConversationUpdate(
        podId: string,
        conversationId: string,
        requestBody: UpdateConversationRequest,
    ): CancelablePromise<ConversationResponse> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/pods/{pod_id}/conversations/{conversation_id}',
            path: {
                'pod_id': podId,
                'conversation_id': conversationId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List Agent Run Approvals
     * List pending user-interaction tool calls (request_approval and ask_user) awaiting the user in a conversation.
     * @param podId
     * @param conversationId
     * @returns UserApprovalListResponse Successful Response
     * @throws ApiError
     */
    public static agentConversationApprovalList(
        podId: string,
        conversationId: string,
    ): CancelablePromise<UserApprovalListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/conversations/{conversation_id}/approvals',
            path: {
                'pod_id': podId,
                'conversation_id': conversationId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Resolve User Approval
     * Record the user's decision/answers for a paused request_approval or ask_user call and start a fresh run that resumes the agent. For an approved request_approval the wrapped tool runs as the user; the response body carries ask_user answers under `response.answers`.
     * @param podId
     * @param conversationId
     * @param approvalId
     * @param requestBody
     * @returns ApprovalDecisionResponse Successful Response
     * @throws ApiError
     */
    public static agentConversationApprovalResolve(
        podId: string,
        conversationId: string,
        approvalId: string,
        requestBody: ResolveUserApprovalRequest,
    ): CancelablePromise<ApprovalDecisionResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/conversations/{conversation_id}/approvals/{approval_id}/decision',
            path: {
                'pod_id': podId,
                'conversation_id': conversationId,
                'approval_id': approvalId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List Pod Conversation Messages
     * List the latest persisted messages in chronological order. Pass next_page_token as page_token to fetch the next older page above the current page.
     * @param podId
     * @param conversationId
     * @param pageToken
     * @param beforeSequence
     * @param afterSequence
     * @param limit
     * @returns MessageListResponse Successful Response
     * @throws ApiError
     */
    public static agentConversationMessageList(
        podId: string,
        conversationId: string,
        pageToken?: (string | null),
        beforeSequence?: (number | null),
        afterSequence?: (number | null),
        limit: number = 100,
    ): CancelablePromise<MessageListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/conversations/{conversation_id}/messages',
            path: {
                'pod_id': podId,
                'conversation_id': conversationId,
            },
            query: {
                'page_token': pageToken,
                'before_sequence': beforeSequence,
                'after_sequence': afterSequence,
                'limit': limit,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Send Pod Conversation Message
     * Append a user message to a pod-scoped conversation and stream runtime events over Server-Sent Events until the active turn completes. User messages can also be appended while work is already active; the next harness step sees the new message in persisted history.
     * @param podId
     * @param conversationId
     * @param requestBody
     * @returns any Successful Response
     * @throws ApiError
     */
    public static agentConversationMessageSend(
        podId: string,
        conversationId: string,
        requestBody: SendMessageRequest,
    ): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/conversations/{conversation_id}/messages',
            path: {
                'pod_id': podId,
                'conversation_id': conversationId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Retry Failed Pod Conversation Run
     * Start a new run from the latest failed run's persisted conversation history without appending a duplicate user message. Retry is allowed only when the failed run produced no assistant, tool, or system activity. Attach to the returned run with the conversation stream endpoint.
     * @param podId
     * @param conversationId
     * @returns AgentRunStartResponse Successful Response
     * @throws ApiError
     */
    public static agentConversationRetry(
        podId: string,
        conversationId: string,
    ): CancelablePromise<AgentRunStartResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/conversations/{conversation_id}/retry',
            path: {
                'pod_id': podId,
                'conversation_id': conversationId,
            },
            errors: {
                404: `Conversation was not found or is not visible`,
                409: `The latest run is not safely retryable`,
                422: `Validation Error`,
                429: `The account usage limit was exceeded`,
            },
        });
    }
    /**
     * Stop Pod Conversation
     * Request cancellation of the active conversation work.
     * @param podId
     * @param conversationId
     * @returns ConversationResponse Successful Response
     * @throws ApiError
     */
    public static agentConversationStop(
        podId: string,
        conversationId: string,
    ): CancelablePromise<ConversationResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/conversations/{conversation_id}/stop',
            path: {
                'pod_id': podId,
                'conversation_id': conversationId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Stream Pod Conversation
     * Subscribe to Server-Sent Events for an existing pod-scoped conversation. The stream closes immediately when the conversation has no active run. Optionally filter to a specific internal run id for reconnects; terminal runs replay their persisted terminal event.
     * @param podId
     * @param conversationId
     * @returns any Successful Response
     * @throws ApiError
     */
    public static agentConversationStream(
        podId: string,
        conversationId: string
    ): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/conversations/{conversation_id}/stream',
            path: {
                'pod_id': podId,
                'conversation_id': conversationId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
