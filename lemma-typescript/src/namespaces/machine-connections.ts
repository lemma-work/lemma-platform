import type { GeneratedClientAdapter } from "../generated.js";
import type { ApprovePairingRequest } from "../openapi_client/models/ApprovePairingRequest.js";
import type { MachineConnectionList } from "../openapi_client/models/MachineConnectionList.js";
import type { MachineConnectionResponse } from "../openapi_client/models/MachineConnectionResponse.js";
import type { PendingPairingResponse } from "../openapi_client/models/PendingPairingResponse.js";
import { MachinesService } from "../openapi_client/services/MachinesService.js";

/**
 * Machines the caller joined to Lemma with `lemma machine connect`.
 *
 * The machine asks for a code and shows it; a signed-in person approves the
 * code here, which decides the machine's name and any pod it is shared into.
 * The machine then claims its own secret over its link.
 */
export class MachineConnectionsNamespace {
  constructor(private readonly client: GeneratedClientAdapter) {}

  list(): Promise<MachineConnectionList> {
    return this.client.request(() => MachinesService.machineConnectionsList());
  }

  /** What the machine showing `code` calls itself, before approving it. */
  pairing(code: string): Promise<PendingPairingResponse> {
    return this.client.request(() => MachinesService.machineConnectionsPairing(code));
  }

  pair(request: ApprovePairingRequest): Promise<MachineConnectionResponse> {
    return this.client.request(() => MachinesService.machineConnectionsPair(request));
  }

  /** Disconnect a machine: its secret stops working and its link closes. */
  delete(connectionId: string): Promise<void> {
    return this.client.request(() => MachinesService.machineConnectionsDelete(connectionId));
  }
}
