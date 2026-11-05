import type { GeneratedClientAdapter } from "../generated.js";
import type { ConnectPodRequest } from "../openapi_client/models/ConnectPodRequest.js";
import { AgentAsksService } from "../openapi_client/services/AgentAsksService.js";

/** What a connected pod may read here, past what is Public. Reading only. */
export interface PodLinkShares {
  /** Table names: the connected pod reads their rows as an outsider would. */
  tables?: string[];
  /** Folder paths. */
  folders?: string[];
}

/**
 * Which other pods a pod's assistant can ask, and which may ask it.
 *
 * A pod the caller is also in can be asked as the caller. A pod connected to
 * this one can ask it with nobody present, and reads what is Public here plus
 * the tables and folders shared with it, never more than reading. Connecting
 * needs `pod.member.manage` here and membership of the other pod; the caller
 * looks after the link. Connecting again replaces what is shared.
 */
export class PodLinksNamespace {
  constructor(private readonly client: GeneratedClientAdapter) {}

  /** The other pods this pod's assistant can ask, and how. */
  askable(podId: string) {
    return this.client.request(() => AgentAsksService.agentAskablePodList(podId));
  }

  /** The other pods connected to this one, with what each was shared. */
  list(podId: string) {
    return this.client.request(() => AgentAsksService.agentPodLinkList(podId));
  }

  /** Let `askingPodId` ask `podId`, reading what is named here as well as what is Public. */
  connect(podId: string, askingPodId: string, shares: PodLinkShares = {}) {
    // The literal resource types are what go on the wire; the generated enum
    // object would only add a runtime value to the bundle for the same string.
    const body = {
      shares: [
        ...(shares.tables ?? []).map((name) => ({
          resource_type: "datastore_table",
          resource_name: name,
          permission_ids: ["datastore.table.read", "datastore.record.read"],
        })),
        ...(shares.folders ?? []).map((path) => ({
          resource_type: "folder",
          resource_name: path,
          permission_ids: ["folder.read"],
        })),
      ],
    } as ConnectPodRequest;
    return this.client.request(() =>
      AgentAsksService.agentPodLinkConnect(podId, askingPodId, body),
    );
  }

  /** Stop `askingPodId` asking `podId`, and take back what it was shared. */
  disconnect(podId: string, askingPodId: string) {
    return this.client.request(() =>
      AgentAsksService.agentPodLinkDisconnect(podId, askingPodId),
    );
  }
}
