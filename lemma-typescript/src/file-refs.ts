/**
 * File arguments for connector operations.
 *
 * An operation that takes a file — a Gmail attachment, a Drive upload — takes a
 * reference to one, and the server reads it with the caller's own access:
 *
 *   client.connectors.operations.execute(scope, "GMAIL_SEND_EMAIL", {
 *     recipient_email: "anukul@lemma.work",
 *     attachment: podFile("/me/reports/q3.pdf"),
 *   });
 *
 * These only build the object; writing it by hand is equally valid.
 */
export type PodFileRef =
  | { pod_path: string; filename?: string }
  | { file_id: string; filename?: string };

/** A file in the pod, by path — `/me/...` is the caller's own folder. */
export function podFile(path: string, filename?: string): PodFileRef {
  return filename ? { pod_path: path, filename } : { pod_path: path };
}

/** A file in the pod, by id: stable across renames, and the same file for
 *  whoever runs the call, where `/me` is not. */
export function podFileById(fileId: string, filename?: string): PodFileRef {
  return filename ? { file_id: fileId, filename } : { file_id: fileId };
}
