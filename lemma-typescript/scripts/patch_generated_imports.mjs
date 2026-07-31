#!/usr/bin/env node

import { readdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const rootDir = process.argv[2];

if (!rootDir) {
  console.error("Usage: patch_generated_imports.mjs <generated-ts-root>");
  process.exit(1);
}

function walk(dir) {
  const out = [];
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    const stat = statSync(full);
    if (stat.isDirectory()) {
      out.push(...walk(full));
      continue;
    }
    if (full.endsWith(".ts")) {
      out.push(full);
    }
  }
  return out;
}

function patchImports(source) {
  return source.replace(
    /from\s+['"](\.{1,2}\/[^'"\n]+)['"]/g,
    (match, specifier) => {
      if (specifier.endsWith(".js") || specifier.endsWith(".json")) {
        return match;
      }
      return match.replace(specifier, `${specifier}.js`);
    },
  );
}

function patchKnownGeneratorIssues(source, filePath) {
  let patched = source;

  if (filePath.endsWith("services/OrganizationsService.ts")) {
    patched = patched.replace(
      "import type { OrganizationInvitationStatus } from '../models/OrganizationInvitationStatus.js';",
      "import { OrganizationInvitationStatus } from '../models/OrganizationInvitationStatus.js';",
    );
    patched = patched.replaceAll(
      "status: OrganizationInvitationStatus = 'PENDING'",
      "status: OrganizationInvitationStatus = OrganizationInvitationStatus.PENDING",
    );
  }

  if (filePath.endsWith("services/FilesService.ts")) {
    patched = patched.replace(
      "import type { FileNamespace } from '../models/FileNamespace.js';",
      "import { FileNamespace } from '../models/FileNamespace.js';",
    );
    patched = patched.replaceAll(
      "namespace: FileNamespace = 'PERSONAL'",
      "namespace: FileNamespace = FileNamespace.PERSONAL",
    );
  }

  if (filePath.endsWith("services/AgentConversationsService.ts")) {
    patched = patched
      .replaceAll(
        "'agent_name': agentName,",
        "'agent_name': agentName === null ? 'POD_DEFAULT' : agentName,",
      )
      .replaceAll(
        "Create or continue a pod-scoped assistant or agent conversation and stream runtime events over Server-Sent Events until the active run completes. Provide agent_name to target a pod agent; omit it for the default pod assistant.",
        "Create or continue a pod-scoped assistant or agent conversation and stream runtime events over Server-Sent Events until the active turn completes. Provide agent_name to target a pod agent; omit it for the default pod assistant.",
      )
      .replaceAll(
        "Append a user message to a pod-scoped conversation and stream runtime events over Server-Sent Events until the active run completes. User messages can also be appended while a run is already active; the next harness step sees the new message in persisted history.",
        "Append a user message to a pod-scoped conversation and stream runtime events over Server-Sent Events until the active turn completes. User messages can also be appended while work is already active; the next harness step sees the new message in persisted history.",
      )
      .replaceAll(
        "Request cancellation of the active internal run for a conversation.",
        "Request cancellation of the active conversation work.",
      )
      .replace(
        /\n    \/\*\*\n     \* Send Pod Conversation Message\n     \* Create or continue a pod-scoped assistant or agent conversation and stream runtime events over Server-Sent Events until the active turn completes\. Provide agent_name to target a pod agent; omit it for the default pod assistant\.\n     \* @param podId\n     \* @param requestBody\n     \* @returns any Successful Response\n     \* @throws ApiError\n     \*\/\n    public static agentConversationMessageSendOrCreate\(\n        podId: string,\n        requestBody: SendMessageRequest,\n    \): CancelablePromise<any> \{\n        return __request\(OpenAPI, \{\n            method: 'POST',\n            url: '\/pods\/\{pod_id\}\/conversations\/messages',\n            path: \{\n                'pod_id': podId,\n            \},\n            body: requestBody,\n            mediaType: 'application\/json',\n            errors: \{\n                422: `Validation Error`,\n            \},\n        \}\);\n    \}\n/g,
        "",
      )
      .replaceAll(
        "Subscribe to Server-Sent Events for an existing pod-scoped conversation. The stream closes immediately when the conversation has no active run. Optionally filter to a specific internal run id for reconnects.",
        "Subscribe to Server-Sent Events for an existing pod-scoped conversation. The stream closes immediately when the conversation has no active work.",
      )
      .replace(/\s+\* @param agentRunId/g, "")
      .replace(/,\n\s+agentRunId\?: \(string \| null\),/g, "")
      .replace(/\n\s+query: \{\n\s+'agent_run_id': agentRunId,\n\s+\},/g, "");
  }

  if (filePath.endsWith("models/MessageResponse.ts")) {
    patched = patched.replace(/\n\s+agent_run_id\?: \(string \| null\);/g, "");
  }

  if (filePath.endsWith("models/AgentModelName.ts")) {
    patched = patched.replace(
      "Models that can be selected for an agent run.",
      "Models that can be selected for an agent conversation.",
    );
  }

  // The generator has emitted either one or two final newlines across runtime
  // versions. Canonicalize the ending so codegen drift checks are byte-stable.
  return patched.replace(/\n+$/, "\n");
}

let updated = 0;
for (const file of walk(rootDir)) {
  const before = readFileSync(file, "utf8");
  const after = patchKnownGeneratorIssues(patchImports(before), file);
  if (before !== after) {
    writeFileSync(file, after, "utf8");
    updated += 1;
  }
}

console.log(`Patched ESM imports in ${updated} generated files`);
