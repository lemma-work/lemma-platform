import type { GeneratedClientAdapter } from "../generated.js";
import type { AgentHostHarnessListResponse } from "../openapi_client/models/AgentHostHarnessListResponse.js";
import type { AgentHostListResponse } from "../openapi_client/models/AgentHostListResponse.js";
import type { AgentHostPairingCreate } from "../openapi_client/models/AgentHostPairingCreate.js";
import type { AgentHostPairingCreated } from "../openapi_client/models/AgentHostPairingCreated.js";
import type { AgentHostResponse } from "../openapi_client/models/AgentHostResponse.js";
import { AgentHostService } from "../openapi_client/services/AgentHostService.js";

/**
 * Manage the caller's paired Agent Hosts.
 *
 * Only the user-authenticated management routes live here. The Agent Host
 * binary itself speaks to Lemma over its link WebSocket (`/agent-host/link`),
 * authenticated by its own host secret rather than a browser session, so it has
 * no HTTP operations to expose.
 */
export class AgentHostNamespace {
  constructor(private readonly client: GeneratedClientAdapter) {}

  list(): Promise<AgentHostListResponse> {
    return this.client.request(() => AgentHostService.agentHostList());
  }

  createPairing(request: AgentHostPairingCreate): Promise<AgentHostPairingCreated> {
    return this.client.request(() => AgentHostService.agentHostPairingCreate(request));
  }

  listHarnesses(hostId: string): Promise<AgentHostHarnessListResponse> {
    return this.client.request(() => AgentHostService.agentHostHarnessesList(hostId));
  }

  revoke(hostId: string): Promise<AgentHostResponse> {
    return this.client.request(() => AgentHostService.agentHostRevoke(hostId));
  }
}
