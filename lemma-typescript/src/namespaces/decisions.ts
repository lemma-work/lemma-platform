import type { GeneratedClientAdapter } from "../generated.js";
import type { DecideBody } from "../openapi_client/models/DecideBody.js";
import type { DecideRowsBody } from "../openapi_client/models/DecideRowsBody.js";
import type { DeciderDefinition } from "../openapi_client/models/DeciderDefinition.js";
import type { DeciderTestBody } from "../openapi_client/models/DeciderTestBody.js";
import type { AnswerBody } from "../openapi_client/models/AnswerBody.js";
import { DecisionsService } from "../openapi_client/services/DecisionsService.js";

/** Asks one decision: a pod decider by name, `system:<name>`, or questions inline. */
export type DecideRequest = Omit<DecideBody, "visibility"> & {
  /** Personal unless the state is the pod's to share: the decision keeps it as evidence. */
  visibility?: "PERSONAL" | "POD";
};

export type DecideRowsRequest = Omit<DecideRowsBody, "visibility"> & {
  visibility?: "PERSONAL" | "POD";
};

export interface DecisionListOptions {
  decider?: string | null;
  openOnly?: boolean;
  /** An ISO timestamp: only decisions made before it. */
  before?: string | null;
  limit?: number;
}

/**
 * Decisions in this pod: closed-set judgements asked about one piece of state
 * -- which of these, yes or no, how much. The first model capability an app
 * can use without starting an agent.
 */
export class DecisionsNamespace {
  constructor(
    private readonly client: GeneratedClientAdapter,
    private readonly podId: () => string,
  ) {}

  /** With a `subject`, the decider is asked about it once; asking again returns the record. */
  decide(request: DecideRequest) {
    return this.client.request(() =>
      DecisionsService.decisionCreate(this.podId(), {
        ...request,
        visibility: (request.visibility ?? "PERSONAL") as DecideBody.visibility,
      }),
    );
  }

  decideRows(request: DecideRowsRequest) {
    return this.client.request(() =>
      DecisionsService.decisionRows(this.podId(), {
        ...request,
        visibility: (request.visibility ?? "PERSONAL") as DecideRowsBody.visibility,
      }),
    );
  }

  /** Newest first: the pod's shared decisions and your own. */
  list(options: DecisionListOptions = {}) {
    return this.client.request(() =>
      DecisionsService.decisionList(
        this.podId(),
        options.decider,
        options.openOnly ?? false,
        options.before,
        options.limit ?? 50,
      ),
    );
  }

  get(decisionId: string) {
    return this.client.request(() => DecisionsService.decisionGet(this.podId(), decisionId));
  }

  /** Answer an open question or correct a machine's answer; it becomes an example. */
  answer(decisionId: string, answers: Record<string, string | string[] | boolean | number>) {
    return this.client.request(() =>
      // The literal, not `AnswerBody.by.PERSON`: a value import would bundle
      // the generated enum object for one string.
      DecisionsService.decisionAnswer(this.podId(), decisionId, { answers, by: "person" as AnswerBody.by }),
    );
  }
}

/** A pod's deciders: named, versioned judgements its decisions are asked of. */
export class DecidersNamespace {
  constructor(
    private readonly client: GeneratedClientAdapter,
    private readonly podId: () => string,
  ) {}

  list(limit = 100) {
    return this.client.request(() => DecisionsService.deciderList(this.podId(), limit));
  }

  create(name: string, definition: DeciderDefinition) {
    return this.client.request(() => DecisionsService.deciderCreate(this.podId(), { name, definition }));
  }

  get(name: string) {
    return this.client.request(() => DecisionsService.deciderGet(this.podId(), name));
  }

  /** Saves a new version; the old one is kept, and decisions name theirs. */
  update(name: string, definition: DeciderDefinition) {
    return this.client.request(() => DecisionsService.deciderUpdate(this.podId(), name, { definition }));
  }

  delete(name: string) {
    return this.client.request(() => DecisionsService.deciderDelete(this.podId(), name));
  }

  versions(name: string, limit = 50) {
    return this.client.request(() => DecisionsService.deciderVersionList(this.podId(), name, limit));
  }

  /** Decides sample rows without recording anything, compared with expected answers. */
  test(request: Pick<DeciderTestBody, "decider" | "definition" | "rows" | "options">) {
    return this.client.request(() => DecisionsService.deciderTest(this.podId(), request));
  }
}
