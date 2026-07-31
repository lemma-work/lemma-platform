/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AccountCreateSchema } from '../models/AccountCreateSchema.js';
import type { AccountListResponseSchema } from '../models/AccountListResponseSchema.js';
import type { AccountResponseSchema } from '../models/AccountResponseSchema.js';
import type { AppTriggerListResponseSchema } from '../models/AppTriggerListResponseSchema.js';
import type { AppTriggerResponseSchema } from '../models/AppTriggerResponseSchema.js';
import type { AuthConfigCreateSchema } from '../models/AuthConfigCreateSchema.js';
import type { AuthConfigListResponseSchema } from '../models/AuthConfigListResponseSchema.js';
import type { AuthConfigResponseSchema } from '../models/AuthConfigResponseSchema.js';
import type { ConnectorDetailResponseSchema } from '../models/ConnectorDetailResponseSchema.js';
import type { ConnectorListResponseSchema } from '../models/ConnectorListResponseSchema.js';
import type { ConnectorSkillResponse } from '../models/ConnectorSkillResponse.js';
import type { ConnectorStatusResponse } from '../models/ConnectorStatusResponse.js';
import type { ConnectRequestInitiateSchema } from '../models/ConnectRequestInitiateSchema.js';
import type { ConnectRequestResponseSchema } from '../models/ConnectRequestResponseSchema.js';
import type { MessageResponseSchema } from '../models/MessageResponseSchema.js';
import type { OperationDetail } from '../models/OperationDetail.js';
import type { OperationDetailsBatchRequest } from '../models/OperationDetailsBatchRequest.js';
import type { OperationDetailsBatchResponse } from '../models/OperationDetailsBatchResponse.js';
import type { OperationDiscoverResponse } from '../models/OperationDiscoverResponse.js';
import type { OperationExecutionRequest } from '../models/OperationExecutionRequest.js';
import type { OperationExecutionResponse } from '../models/OperationExecutionResponse.js';
import type { CancelablePromise } from '../core/CancelablePromise.js';
import { OpenAPI } from '../core/OpenAPI.js';
import { request as __request } from '../core/request.js';
export class ConnectorsService {
    /**
     * List Connectors
     * Get all active connectors available for connector
     * @param limit
     * @param pageToken
     * @returns ConnectorListResponseSchema Successful Response
     * @throws ApiError
     */
    public static connectorList(
        limit: number = 100,
        pageToken?: (string | null),
    ): CancelablePromise<ConnectorListResponseSchema> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/connectors',
            query: {
                'limit': limit,
                'page_token': pageToken,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * OAuth Callback
     * Handle OAuth callback and complete account connection. This endpoint is public and uses state parameter for security.
     * @param error
     * @param format
     * @returns string Successful Response
     * @throws ApiError
     */
    public static connectorOauthCallback(
        error?: (string | null),
        format?: (string | null),
    ): CancelablePromise<string> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/connectors/connect-requests/oauth/callback',
            query: {
                'error': error,
                'format': format,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Connector
     * Get a specific connector by ID along with its operation catalog
     * @param connectorId
     * @returns ConnectorDetailResponseSchema Successful Response
     * @throws ApiError
     */
    public static connectorGet(
        connectorId: string,
    ): CancelablePromise<ConnectorDetailResponseSchema> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/connectors/{connector_id}',
            path: {
                'connector_id': connectorId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Connector Skill
     * Get the skill guide markdown for a connector. Pass `provider=lemma` or `provider=composio` to get provider-specific instructions when the app supports both. Falls back to the generic doc if no provider-specific file exists. Returns 404 if no skill doc has been generated yet.
     * @param connectorId
     * @param provider Provider override: lemma or composio
     * @returns ConnectorSkillResponse Successful Response
     * @throws ApiError
     */
    public static connectorSkillGet(
        connectorId: string,
        provider?: (string | null),
    ): CancelablePromise<ConnectorSkillResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/connectors/{connector_id}/skill',
            path: {
                'connector_id': connectorId,
            },
            query: {
                'provider': provider,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List Accounts
     * Get all connected accounts for the current user. Optionally filter by connector_id or connector_name
     * @param organizationId
     * @param connectorId
     * @param limit
     * @param pageToken
     * @returns AccountListResponseSchema Successful Response
     * @throws ApiError
     */
    public static connectorAccountList(
        organizationId: string,
        connectorId?: (string | null),
        limit: number = 100,
        pageToken?: (string | null),
    ): CancelablePromise<AccountListResponseSchema> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/organizations/{organization_id}/connectors/accounts',
            path: {
                'organization_id': organizationId,
            },
            query: {
                'connector_id': connectorId,
                'limit': limit,
                'page_token': pageToken,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Create Account
     * Directly connect a credential-managed native account for an org auth config.
     * @param organizationId
     * @param requestBody
     * @returns AccountResponseSchema Successful Response
     * @throws ApiError
     */
    public static connectorAccountCreate(
        organizationId: string,
        requestBody: AccountCreateSchema,
    ): CancelablePromise<AccountResponseSchema> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/organizations/{organization_id}/connectors/accounts',
            path: {
                'organization_id': organizationId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Delete Account
     * Delete a connected account and revoke the connection
     * @param organizationId
     * @param accountId
     * @returns MessageResponseSchema Successful Response
     * @throws ApiError
     */
    public static connectorAccountDelete(
        organizationId: string,
        accountId: string,
    ): CancelablePromise<MessageResponseSchema> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/organizations/{organization_id}/connectors/accounts/{account_id}',
            path: {
                'organization_id': organizationId,
                'account_id': accountId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Account
     * Get a specific account by ID
     * @param organizationId
     * @param accountId
     * @returns AccountResponseSchema Successful Response
     * @throws ApiError
     */
    public static connectorAccountGet(
        organizationId: string,
        accountId: string,
    ): CancelablePromise<AccountResponseSchema> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/organizations/{organization_id}/connectors/accounts/{account_id}',
            path: {
                'organization_id': organizationId,
                'account_id': accountId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List Auth Configs
     * @param organizationId
     * @param limit
     * @param pageToken
     * @returns AuthConfigListResponseSchema Successful Response
     * @throws ApiError
     */
    public static connectorAuthConfigList(
        organizationId: string,
        limit: number = 100,
        pageToken?: (string | null),
    ): CancelablePromise<AuthConfigListResponseSchema> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/organizations/{organization_id}/connectors/auth-configs',
            path: {
                'organization_id': organizationId,
            },
            query: {
                'limit': limit,
                'page_token': pageToken,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Create Auth Config
     * @param organizationId
     * @param requestBody
     * @returns AuthConfigResponseSchema Successful Response
     * @throws ApiError
     */
    public static connectorAuthConfigCreate(
        organizationId: string,
        requestBody: AuthConfigCreateSchema,
    ): CancelablePromise<AuthConfigResponseSchema> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/organizations/{organization_id}/connectors/auth-configs',
            path: {
                'organization_id': organizationId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Delete Auth Config
     * @param organizationId
     * @param authConfigName
     * @returns boolean Successful Response
     * @throws ApiError
     */
    public static connectorAuthConfigDelete(
        organizationId: string,
        authConfigName: string,
    ): CancelablePromise<Record<string, boolean>> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/organizations/{organization_id}/connectors/auth-configs/{auth_config_name}',
            path: {
                'organization_id': organizationId,
                'auth_config_name': authConfigName,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Auth Config
     * @param organizationId
     * @param authConfigName
     * @returns AuthConfigResponseSchema Successful Response
     * @throws ApiError
     */
    public static connectorAuthConfigGet(
        organizationId: string,
        authConfigName: string,
    ): CancelablePromise<AuthConfigResponseSchema> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/organizations/{organization_id}/connectors/auth-configs/{auth_config_name}',
            path: {
                'organization_id': organizationId,
                'auth_config_name': authConfigName,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Initiate Connect Request
     * Initiate an OAuth connection request for a connector
     * @param organizationId
     * @param requestBody
     * @returns ConnectRequestResponseSchema Successful Response
     * @throws ApiError
     */
    public static connectorConnectRequestCreate(
        organizationId: string,
        requestBody: ConnectRequestInitiateSchema,
    ): CancelablePromise<ConnectRequestResponseSchema> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/organizations/{organization_id}/connectors/connect-requests',
            path: {
                'organization_id': organizationId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Connector Status
     * @param organizationId
     * @returns ConnectorStatusResponse Successful Response
     * @throws ApiError
     */
    public static connectorStatusGet(
        organizationId: string,
    ): CancelablePromise<ConnectorStatusResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/organizations/{organization_id}/connectors/status',
            path: {
                'organization_id': organizationId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Discover Connector Operations
     * @param organizationId
     * @param authConfigName
     * @param query
     * @param limit
     * @returns OperationDiscoverResponse Successful Response
     * @throws ApiError
     */
    public static connectorOperationDiscover(
        organizationId: string,
        authConfigName: string,
        query?: (string | null),
        limit: number = 100,
    ): CancelablePromise<OperationDiscoverResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/organizations/{organization_id}/connectors/{auth_config_name}/operations',
            path: {
                'organization_id': organizationId,
                'auth_config_name': authConfigName,
            },
            query: {
                'query': query,
                'limit': limit,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Connector Operation Details In Batch
     * @param organizationId
     * @param authConfigName
     * @param requestBody
     * @returns OperationDetailsBatchResponse Successful Response
     * @throws ApiError
     */
    public static connectorOperationDetailsBatch(
        organizationId: string,
        authConfigName: string,
        requestBody: OperationDetailsBatchRequest,
    ): CancelablePromise<OperationDetailsBatchResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/organizations/{organization_id}/connectors/{auth_config_name}/operations/details',
            path: {
                'organization_id': organizationId,
                'auth_config_name': authConfigName,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Connector Operation Details
     * @param organizationId
     * @param authConfigName
     * @param operationName
     * @returns OperationDetail Successful Response
     * @throws ApiError
     */
    public static connectorOperationDetail(
        organizationId: string,
        authConfigName: string,
        operationName: string,
    ): CancelablePromise<OperationDetail> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/organizations/{organization_id}/connectors/{auth_config_name}/operations/{operation_name}',
            path: {
                'organization_id': organizationId,
                'auth_config_name': authConfigName,
                'operation_name': operationName,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Execute Connector Operation
     * @param organizationId
     * @param authConfigName
     * @param operationName
     * @param requestBody
     * @returns OperationExecutionResponse Successful Response
     * @throws ApiError
     */
    public static connectorOperationExecute(
        organizationId: string,
        authConfigName: string,
        operationName: string,
        requestBody: OperationExecutionRequest,
    ): CancelablePromise<OperationExecutionResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/organizations/{organization_id}/connectors/{auth_config_name}/operations/{operation_name}/execute',
            path: {
                'organization_id': organizationId,
                'auth_config_name': authConfigName,
                'operation_name': operationName,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List Connector Triggers
     * @param organizationId
     * @param authConfigName
     * @param search
     * @param limit
     * @returns AppTriggerListResponseSchema Successful Response
     * @throws ApiError
     */
    public static connectorTriggerList(
        organizationId: string,
        authConfigName: string,
        search?: (string | null),
        limit: number = 100,
    ): CancelablePromise<AppTriggerListResponseSchema> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/organizations/{organization_id}/connectors/{auth_config_name}/triggers',
            path: {
                'organization_id': organizationId,
                'auth_config_name': authConfigName,
            },
            query: {
                'search': search,
                'limit': limit,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Connector Trigger
     * @param organizationId
     * @param authConfigName
     * @param triggerName
     * @returns AppTriggerResponseSchema Successful Response
     * @throws ApiError
     */
    public static connectorTriggerGet(
        organizationId: string,
        authConfigName: string,
        triggerName: string,
    ): CancelablePromise<AppTriggerResponseSchema> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/organizations/{organization_id}/connectors/{auth_config_name}/triggers/{trigger_name}',
            path: {
                'organization_id': organizationId,
                'auth_config_name': authConfigName,
                'trigger_name': triggerName,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
