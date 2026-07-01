export const TABLES = {
  commitments: 'commitments',
  people:      'people',
  noteIndex:   'note_index',
  pings:       'pings',
  pingReplies: 'ping_replies',
} as const;

export const AGENTS = {
  mrToot: 'mr-toot',
} as const;

export const FUNCTIONS = {
  sendPing:       'send_ping',
  importContacts: 'import_contacts',
  pollReplies:    'poll_replies',
} as const;
