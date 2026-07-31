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
 * Only the user-authenticated management half of `/agent-host` lives here. The
 * host-authenticated half (poll, events:append, harness publish, pairing
 * completion) is spoken by the Agent Host binary with its own host secret, not
 * by a browser session, so it stays off this namespace.
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
