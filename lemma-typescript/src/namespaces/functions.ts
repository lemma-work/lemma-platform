import type { GeneratedClientAdapter } from "../generated.js";
import type { CreateFunctionRequest } from "../openapi_client/models/CreateFunctionRequest.js";
import type { ExecuteFunctionRequest } from "../openapi_client/models/ExecuteFunctionRequest.js";
import type { FunctionPermissionsReplaceRequest } from "../openapi_client/models/FunctionPermissionsReplaceRequest.js";
import type { UpdateFunctionRequest } from "../openapi_client/models/UpdateFunctionRequest.js";
import { FunctionsService } from "../openapi_client/services/FunctionsService.js";
import type { RunFunctionOptions } from "../types.js";

export class FunctionsNamespace {
  constructor(private readonly client: GeneratedClientAdapter, private readonly podId: () => string) {}

  list(options: { limit?: number; pageToken?: string } = {}) {
    return this.client.request(() => FunctionsService.functionList(this.podId(), options.limit ?? 100, options.pageToken));
  }
  create(payload: CreateFunctionRequest) {
    return this.client.request(() => FunctionsService.functionCreate(this.podId(), payload));
  }
  get(name: string) {
    return this.client.request(() => FunctionsService.functionGet(this.podId(), name));
  }
  update(name: string, payload: UpdateFunctionRequest) {
    return this.client.request(() => FunctionsService.functionUpdate(this.podId(), name, payload));
  }
  delete(name: string) {
    return this.client.request(() => FunctionsService.functionDelete(this.podId(), name));
  }

  /** Run a function — convenience alias for `functions.runs.create`, matching the
   *  Python SDK's `functions.run(name, input)` and the unified `.run` verb. */
  run(name: string, options: RunFunctionOptions = {}) {
    return this.runs.create(name, options);
  }

  readonly permissions = {
    get: (name: string) =>
      this.client.request(() => FunctionsService.functionPermissionsGet(this.podId(), name)),

    replace: (name: string, payload: FunctionPermissionsReplaceRequest) =>
      this.client.request(() => FunctionsService.functionPermissionsReplace(this.podId(), name, payload)),
  };

  readonly revisions = {
    /** This function's built revisions, newest first. */
    list: (name: string) =>
      this.client.request(() => FunctionsService.functionRevisionList(this.podId(), name)),

    /** One revision, with its source and the schemas its code implements. */
    get: (name: string, revisionRef: string) =>
      this.client.request(() => FunctionsService.functionRevisionGet(this.podId(), name, revisionRef)),

    /**
     * Make an existing revision live. Its schemas are restored with it, since
     * they are the contract its code implements; the response reports whether
     * that contract differs from the one that was live.
     */
    promote: (name: string, revisionRef: string) =>
      this.client.request(() => FunctionsService.functionRevisionPromote(this.podId(), name, revisionRef)),
  };

  readonly runs = {
    create: (name: string, options: RunFunctionOptions = {}) =>
      this.client.request(() => {
        const payload: ExecuteFunctionRequest = {
          input_data: options.input as ExecuteFunctionRequest["input_data"],
          // Runs a specific built revision instead of the live one. Requires
          // function.update -- running a superseded build is an authoring
          // action, not an execution one.
          revision: options.revision,
        };
        return FunctionsService.functionRun(this.podId(), name, payload);
      }),
    list: (name: string, params: { limit?: number; pageToken?: string } = {}) =>
      this.client.request(() => FunctionsService.functionRunList(this.podId(), name, params.limit ?? 100, params.pageToken)),
    get: (name: string, runId: string) =>
      this.client.request(() => FunctionsService.functionRunGet(this.podId(), name, runId)),
  };
}
