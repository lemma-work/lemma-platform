/* What "allow for this conversation" allows, in words.

   A session approval covers the permission, not the one call: approving one
   record's delete for the conversation lets the agent delete records in any
   table, without asking, for the rest of it. That is the design, and it is
   only fair if the button says so — "Approve for this conversation" read as
   "this delete, and the next one like it".

   The backend keeps the same table for the cards it posts to Slack, Teams and
   Telegram (`app/core/authorization/approval_wording.py`); a backend test holds
   the two to the same words. */

const SCOPE: Record<string, string> = {
    "datastore.table.read": "read any table",
    "datastore.table.create": "create tables",
    "datastore.table.update": "change any table's columns and settings",
    "datastore.table.delete": "delete any table",
    "datastore.record.read": "read records in any table",
    "datastore.record.write": "write records in any table",
    "datastore.record.delete": "delete records in any table",
    "folder.read": "read any folder or file",
    "folder.write": "write in any folder",
    "folder.delete": "delete any folder or file",
    "folder.share": "make public links to any file",
    "app.update": "change any app",
    "app.delete": "delete any app",
    "app.publish": "publish any app",
    "agent.execute": "run any agent",
    "agent.update": "change any agent",
    "agent.delete": "delete any agent",
    "function.execute": "run any function",
    "function.update": "change any function",
    "function.delete": "delete any function",
    "workflow.execute": "run any workflow",
    "workflow.update": "change any workflow",
    "workflow.delete": "delete any workflow",
    "schedule.update": "change any schedule",
    "schedule.delete": "delete any schedule",
    "connector.use": "use any of the pod's connectors",
    "connector_account.use": "use any account shared with the pod",
    "pod.delete": "delete the pod",
};

export const FOR_THE_CONVERSATION = "for the rest of this conversation";

/** One permission as what it lets the agent do. An id this table does not
 *  know is shown as it is rather than guessed at. */
export function permissionScope(permissionId: string): string {
    return SCOPE[permissionId] ?? permissionId;
}

/** "Delete records in any table for the rest of this conversation". With no
 *  permission named, a session approval covers only the exact call, again. */
export function sessionScopeSentence(permissionIds: readonly string[]): string {
    const phrases = [...new Set([...permissionIds].filter(Boolean).sort().map(permissionScope))];
    if (phrases.length === 0) return "Run this exact command again without asking, " + FOR_THE_CONVERSATION;
    const joined = phrases.length === 1
        ? phrases[0]
        : phrases.slice(0, -1).join(", ") + " and " + phrases[phrases.length - 1];
    return joined.charAt(0).toUpperCase() + joined.slice(1) + " " + FOR_THE_CONVERSATION;
}

/** Approving a named agent's request for a connector gives it the connector
 *  for good — once or not — so the card says that instead. */
export function grantNote(connector: string): string {
    const what = connector ? "the " + connector + " connector" : "this connector";
    return "Approving gives this agent " + what + " from now on, in every conversation, until someone removes it.";
}
