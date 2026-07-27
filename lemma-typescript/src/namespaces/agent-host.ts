import type { GeneratedClientAdapter } from "../generated.js";
import type { AgentHostIntegrationListResponse } from "../openapi_client/models/AgentHostIntegrationListResponse.js";
import type { AgentHostListResponse } from "../openapi_client/models/AgentHostListResponse.js";
import type { AgentHostPairingCreate } from "../openapi_client/models/AgentHostPairingCreate.js";
import type { AgentHostPairingCreated } from "../openapi_client/models/AgentHostPairingCreated.js";
import type { AgentHostResponse } from "../openapi_client/models/AgentHostResponse.js";
import { AgentHostService } from "../openapi_client/services/AgentHostService.js";

export class AgentHostNamespace {
  constructor(private readonly client: GeneratedClientAdapter) {}

  list(): Promise<AgentHostListResponse> {
    return this.client.request(() => AgentHostService.agentHostList());
  }

  createPairing(request: AgentHostPairingCreate): Promise<AgentHostPairingCreated> {
    return this.client.request(() => AgentHostService.agentHostPairingCreate(request));
  }

  listIntegrations(hostId: string): Promise<AgentHostIntegrationListResponse> {
    return this.client.request(() => AgentHostService.agentHostIntegrationsList(hostId));
  }

  revoke(hostId: string): Promise<AgentHostResponse> {
    return this.client.request(() => AgentHostService.agentHostRevoke(hostId));
  }
}
