import type { GeneratedClientAdapter } from "../generated.js";
import type { OpenCallRequest } from "../openapi_client/models/OpenCallRequest.js";
import { CallsService } from "../openapi_client/services/CallsService.js";

/**
 * Live voice calls with the pod's agents. A call is a conversation: `open`
 * returns its id, and the audio runs over the WebSocket at
 * `/pods/{podId}/calls/{callId}/socket`.
 */
export class CallsNamespace {
  constructor(
    private readonly client: GeneratedClientAdapter,
    private readonly podId: () => string,
  ) {}

  /** The voice engines this server can open a call with, the default first. */
  config() {
    return this.client.request(() => CallsService.callConfig(this.podId()));
  }

  /** Open a call with `agent_name`, or the pod's default agent. */
  open(payload: OpenCallRequest = {}) {
    return this.client.request(() => CallsService.callOpen(this.podId(), payload));
  }
}
