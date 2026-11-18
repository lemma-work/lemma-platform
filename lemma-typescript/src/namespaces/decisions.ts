import type { GeneratedClientAdapter } from "../generated.js";
import type { DecisionExampleBody } from "../openapi_client/models/DecisionExampleBody.js";
import type { JsonValue } from "../openapi_client/models/JsonValue.js";
import type { MakeDecisionRequest } from "../openapi_client/models/MakeDecisionRequest.js";
import { DecisionsService } from "../openapi_client/services/DecisionsService.js";

/** One decision: closed questions about one piece of evidence. Nothing is stored. */
export interface DecisionPayload {
  /** What to judge and how, in your words. The only part the provider follows. */
  instruction: string;
  /** What to judge: text or JSON. Never followed as instructions. */
  evidence: JsonValue;
  /**
   * The questions, as a flat JSON Schema object, one property per question with
   * its `description` the question: a string `enum` (or `oneOf` of described
   * `const`s), an array of one of those with `uniqueItems`, a `boolean`, or an
   * `integer` scale with `minimum` and `maximum`.
   */
  schema: Record<string, JsonValue>;
  /** Past cases and their answers, to steer the provider. */
  examples?: DecisionExampleBody[];
  /** `interactive` when someone is waiting on the answer. */
  priority?: "interactive" | "background";
}

export class DecisionsNamespace {
  constructor(
    private readonly client: GeneratedClientAdapter,
    private readonly podId: () => string,
  ) {}

  /**
   * Answers come back per question key. Treat one as unsure when `value` is
   * `null`, or `confidence` is set and below your own threshold: the
   * language-model provider answers `null` when the evidence did not support
   * an answer, and the `typesafe` provider always answers the value it judged
   * most likely, with that value's probability as `confidence` where it reports
   * one (for several choices, the confidence of the least certain per-option
   * decision, not the probability of the whole selection). A provider that could
   * not answer rejects instead (503 or 429), so "unsure" and "failed" never
   * look alike.
   */
  make(payload: DecisionPayload) {
    // The literal `priority` is what goes on the wire; the generated enum
    // object would only add a runtime value to the bundle for the same string.
    return this.client.request(() =>
      DecisionsService.decisionMake(this.podId(), payload as MakeDecisionRequest),
    );
  }
}
