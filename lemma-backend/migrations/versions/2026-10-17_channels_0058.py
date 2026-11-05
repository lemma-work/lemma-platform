"""Channels: the places a pod talks, who is in them, and what was said.

**`external_accounts`** are what a pod's bot speaks through -- a Slack
workspace, a Teams tenant, a WhatsApp number, an inbox, a web widget -- and
**`channel_sources`** the places in them that feed a channel, keyed by the
account so a reinstalled bot keeps its places. **`channels`** are DIRECT (a
member and the pod), CONTACT (a contact and the pod) or GROUP (public or
private). A contact's channel is answered in **`channel_cases`** -- one
episode each, who answers by hand, where it stands, whether it asks for a
person -- and **`channel_case_events`** records every change to one; no
contact's channel exists before this, so none needs a case made for it here.
**`channel_members`** are the members, contacts
and agents in a channel; **`channel_read_state`** how far each member has read.
**`channel_messages`** is the timeline, numbered per channel (`seq`);
**`channel_mentions`** who a line names; **`channel_questions`** a question a
line puts to one person; **`channel_message_deliveries`** each send of a line
outside Lemma, unique per line and target, with **`channel_delivery_attempts`**
each try of one and **`channel_push_pending`** the lines waiting to be pushed
to a member, coalesced per channel; **`channel_line_answers`** which
agents answer a line, in which conversation and run, and with which line.

**`agent_conversations.channel_id`** places a conversation in a channel of its
own pod; a person has one conversation per agent there.

`agent_surface_conversation_links`, `agent_surface_groups`,
`agent_surface_group_messages` and `notifications` go, and queued
`notification.expire` work with them: channels replace them. Groups, web chat
and channels were unreleased, so nothing is carried over. Every member already
in a pod is given their own DIRECT channel, as joining one does from now on.

Revision ID: 0058_channels
Revises: 0057_contacts_for_everyone
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0058_channels"
down_revision = "0057_contacts_for_everyone"
branch_labels = None
depends_on = None

#: The tables this creates, as ``channels.infrastructure.models`` declares
#: them -- generated from the models, so the two cannot drift.
_NEW_TABLES = """
CREATE TABLE external_accounts (
	pod_id UUID NOT NULL,
	platform VARCHAR(20) NOT NULL,
	workspace_key VARCHAR(320) NOT NULL,
	bot_key VARCHAR(255),
	surface_id UUID,
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_external_accounts_key UNIQUE NULLS NOT DISTINCT (pod_id, platform, workspace_key, bot_key),
	CONSTRAINT uq_external_accounts_id_pod UNIQUE (id, pod_id),
	FOREIGN KEY(pod_id) REFERENCES pods (id) ON DELETE CASCADE,
	FOREIGN KEY(surface_id) REFERENCES agent_surfaces (id) ON DELETE SET NULL
);
CREATE INDEX ix_external_accounts_surface ON external_accounts (surface_id);
CREATE TABLE channels (
	pod_id UUID NOT NULL,
	kind VARCHAR(10) NOT NULL,
	visibility VARCHAR(10) NOT NULL,
	title VARCHAR(255),
	topic VARCHAR(500),
	member_user_id UUID,
	contact_id UUID,
	created_by_user_id UUID,
	archived_at TIMESTAMP WITH TIME ZONE,
	agents_answer BOOLEAN DEFAULT true NOT NULL,
	hand_back_after_hours INTEGER DEFAULT 12 NOT NULL,
	current_case_id UUID,
	last_seq BIGINT DEFAULT 0 NOT NULL,
	replies BIGINT DEFAULT 0 NOT NULL,
	last_line_id UUID,
	last_line_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	last_rev BIGINT DEFAULT 0 NOT NULL,
	changed_rev BIGINT DEFAULT 0 NOT NULL,
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT ck_channels_kind CHECK (kind IN ('DIRECT', 'CONTACT', 'GROUP')),
	CONSTRAINT ck_channels_visibility CHECK (visibility IN ('PUBLIC', 'PRIVATE')),
	CONSTRAINT ck_channels_hand_back_after_hours CHECK (hand_back_after_hours BETWEEN 1 AND 720),
	CONSTRAINT ck_channels_kind_names_its_person CHECK ((kind = 'DIRECT') = (member_user_id IS NOT NULL) AND (kind = 'CONTACT') = (contact_id IS NOT NULL)),
	CONSTRAINT ck_channels_only_a_group_is_public CHECK (kind = 'GROUP' OR visibility = 'PRIVATE'),
	CONSTRAINT uq_channels_id_pod UNIQUE (id, pod_id),
	CONSTRAINT uq_channels_id_pod_kind UNIQUE (id, pod_id, kind),
	FOREIGN KEY(pod_id) REFERENCES pods (id) ON DELETE CASCADE,
	FOREIGN KEY(member_user_id) REFERENCES users (id) ON DELETE CASCADE,
	FOREIGN KEY(contact_id) REFERENCES contacts (id) ON DELETE CASCADE,
	FOREIGN KEY(created_by_user_id) REFERENCES users (id) ON DELETE SET NULL
);
CREATE INDEX ix_channels_agents_off ON channels (pod_id) WHERE kind = 'CONTACT' AND NOT agents_answer;
CREATE INDEX ix_channels_created_by ON channels (created_by_user_id) WHERE created_by_user_id IS NOT NULL;
CREATE INDEX ix_channels_pod_activity ON channels (pod_id, last_line_at, id);
CREATE UNIQUE INDEX uq_channels_contact ON channels (contact_id);
CREATE UNIQUE INDEX uq_channels_direct_member ON channels (member_user_id, pod_id);
CREATE TABLE channel_cases (
	channel_id UUID NOT NULL,
	pod_id UUID NOT NULL,
	channel_kind VARCHAR(10) DEFAULT 'CONTACT' NOT NULL,
	opened_at TIMESTAMP WITH TIME ZONE NOT NULL,
	opened_seq BIGINT NOT NULL,
	status VARCHAR(20) DEFAULT 'open' NOT NULL,
	assignee_user_id UUID,
	assigned_at TIMESTAMP WITH TIME ZONE,
	assignee_last_line_at TIMESTAMP WITH TIME ZONE,
	attention_at TIMESTAMP WITH TIME ZONE,
	attention_reason VARCHAR(255),
	acknowledged_at TIMESTAMP WITH TIME ZONE,
	first_response_at TIMESTAMP WITH TIME ZONE,
	first_response_by_user_id UUID,
	closed_at TIMESTAMP WITH TIME ZONE,
	closed_by_user_id UUID,
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT ck_channel_cases_status CHECK (status IN ('open', 'waiting_on_contact', 'closed')),
	CONSTRAINT ck_channel_cases_closed_when_closed CHECK ((status = 'closed') = (closed_at IS NOT NULL)),
	CONSTRAINT ck_channel_cases_contact_only CHECK (channel_kind = 'CONTACT'),
	FOREIGN KEY(channel_id, pod_id, channel_kind) REFERENCES channels (id, pod_id, kind) ON DELETE CASCADE,
	CONSTRAINT uq_channel_cases_id_channel UNIQUE (id, channel_id),
	FOREIGN KEY(assignee_user_id) REFERENCES users (id) ON DELETE SET NULL,
	FOREIGN KEY(first_response_by_user_id) REFERENCES users (id) ON DELETE SET NULL,
	FOREIGN KEY(closed_by_user_id) REFERENCES users (id) ON DELETE SET NULL
);
CREATE INDEX ix_channel_cases_assignee ON channel_cases (assignee_user_id) WHERE assignee_user_id IS NOT NULL;
CREATE INDEX ix_channel_cases_channel ON channel_cases (channel_id, opened_at);
CREATE INDEX ix_channel_cases_closed_by ON channel_cases (closed_by_user_id) WHERE closed_by_user_id IS NOT NULL;
CREATE INDEX ix_channel_cases_first_response_by ON channel_cases (first_response_by_user_id) WHERE first_response_by_user_id IS NOT NULL;
CREATE INDEX ix_channel_cases_needs_person ON channel_cases (pod_id) WHERE status = 'open' AND assignee_user_id IS NULL AND attention_at IS NOT NULL;
CREATE UNIQUE INDEX uq_channel_cases_open ON channel_cases (channel_id) WHERE closed_at IS NULL;
CREATE TABLE channel_case_events (
	id BIGINT GENERATED ALWAYS AS IDENTITY,
	case_id UUID NOT NULL,
	at TIMESTAMP WITH TIME ZONE DEFAULT clock_timestamp() NOT NULL,
	kind VARCHAR(20) NOT NULL,
	actor_user_id UUID,
	value JSONB,
	PRIMARY KEY (id),
	CONSTRAINT ck_channel_case_events_kind CHECK (kind IN ('opened', 'assigned', 'handed_back', 'attention', 'acknowledged', 'first_response', 'status', 'closed', 'reopened', 'merged')),
	FOREIGN KEY(case_id) REFERENCES channel_cases (id) ON DELETE CASCADE
);
CREATE INDEX ix_channel_case_events_case ON channel_case_events (case_id, id);
ALTER TABLE channels ADD CONSTRAINT fk_channels_current_case FOREIGN KEY(current_case_id, id) REFERENCES channel_cases (id, channel_id) ON DELETE SET NULL (current_case_id);
CREATE TABLE channel_members (
	channel_id UUID NOT NULL,
	pod_id UUID NOT NULL,
	user_id UUID,
	contact_id UUID,
	agent_id UUID,
	role VARCHAR(10) DEFAULT 'member' NOT NULL,
	notify VARCHAR(10) DEFAULT 'ALL' NOT NULL,
	notify_via TEXT[],
	muted_until TIMESTAMP WITH TIME ZONE,
	answers VARCHAR(12),
	added_by_user_id UUID,
	seated_by VARCHAR(10) NOT NULL,
	last_spoke_at TIMESTAMP WITH TIME ZONE,
	last_name VARCHAR(255),
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT ck_channel_members_one_principal CHECK (num_nonnulls(user_id, contact_id, agent_id) = 1),
	CONSTRAINT ck_channel_members_role CHECK (role IN ('owner', 'member', 'guest')),
	CONSTRAINT ck_channel_members_notify CHECK (notify IN ('ALL', 'MENTIONS', 'NONE')),
	CONSTRAINT ck_channel_members_agent_answers CHECK ((agent_id IS NOT NULL) = (answers IS NOT NULL) AND (answers IS NULL OR answers IN ('MENTIONED', 'EVERY_LINE'))),
	CONSTRAINT ck_channel_members_seated_by CHECK (seated_by IN ('PLATFORM', 'SPOKE', 'ADDED', 'CREATOR', 'JOINED')),
	FOREIGN KEY(channel_id, pod_id) REFERENCES channels (id, pod_id) ON DELETE CASCADE,
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE,
	FOREIGN KEY(contact_id) REFERENCES contacts (id) ON DELETE CASCADE,
	FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE CASCADE,
	FOREIGN KEY(added_by_user_id) REFERENCES users (id) ON DELETE SET NULL
);
CREATE INDEX ix_channel_members_added_by ON channel_members (added_by_user_id) WHERE added_by_user_id IS NOT NULL;
CREATE INDEX ix_channel_members_agent ON channel_members (agent_id) WHERE agent_id IS NOT NULL;
CREATE INDEX ix_channel_members_channel ON channel_members (channel_id, created_at, id);
CREATE INDEX ix_channel_members_contact ON channel_members (contact_id) WHERE contact_id IS NOT NULL;
CREATE INDEX ix_channel_members_user ON channel_members (user_id, pod_id) WHERE user_id IS NOT NULL;
CREATE UNIQUE INDEX uq_channel_members_agent ON channel_members (channel_id, agent_id) WHERE agent_id IS NOT NULL;
CREATE UNIQUE INDEX uq_channel_members_contact ON channel_members (channel_id, contact_id) WHERE contact_id IS NOT NULL;
CREATE UNIQUE INDEX uq_channel_members_user ON channel_members (channel_id, user_id) WHERE user_id IS NOT NULL;
CREATE TABLE channel_read_state (
	user_id UUID NOT NULL,
	channel_id UUID NOT NULL,
	pod_id UUID NOT NULL,
	last_read_seq BIGINT DEFAULT 0 NOT NULL,
	read_replies BIGINT DEFAULT 0 NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (user_id, channel_id),
	FOREIGN KEY(channel_id, pod_id) REFERENCES channels (id, pod_id) ON DELETE CASCADE,
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);
CREATE INDEX ix_channel_read_state_channel ON channel_read_state (channel_id);
CREATE TABLE channel_sources (
	channel_id UUID NOT NULL,
	pod_id UUID NOT NULL,
	account_id UUID NOT NULL,
	platform VARCHAR(20) NOT NULL,
	kind VARCHAR(10) NOT NULL,
	is_private BOOLEAN DEFAULT true NOT NULL,
	external_channel_id TEXT,
	external_user_id TEXT,
	title VARCHAR(255),
	last_inbound_at TIMESTAMP WITH TIME ZONE,
	delivery_ref JSONB,
	owner_user_id UUID,
	answers_outsiders BOOLEAN DEFAULT true NOT NULL,
	shared_externally BOOLEAN DEFAULT false NOT NULL,
	invite_link VARCHAR(512),
	request_id VARCHAR(255),
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT ck_channel_sources_kind CHECK (kind IN ('PERSON', 'CHAT')),
	CONSTRAINT ck_channel_sources_settings_on_a_chat CHECK (kind = 'CHAT' OR (owner_user_id IS NULL AND invite_link IS NULL AND request_id IS NULL)),
	CONSTRAINT uq_channel_sources_id_channel UNIQUE (id, channel_id),
	FOREIGN KEY(channel_id, pod_id) REFERENCES channels (id, pod_id) ON DELETE CASCADE,
	FOREIGN KEY(account_id, pod_id) REFERENCES external_accounts (id, pod_id) ON DELETE CASCADE,
	FOREIGN KEY(owner_user_id) REFERENCES users (id) ON DELETE SET NULL
);
CREATE INDEX ix_channel_sources_channel ON channel_sources (channel_id);
CREATE INDEX ix_channel_sources_account ON channel_sources (account_id);
CREATE INDEX ix_channel_sources_shared ON channel_sources (pod_id, updated_at) WHERE kind = 'CHAT';
CREATE INDEX ix_channel_sources_chat ON channel_sources (external_channel_id, platform) WHERE external_user_id IS NULL;
CREATE INDEX ix_channel_sources_person ON channel_sources (external_user_id, platform) WHERE external_user_id IS NOT NULL;
CREATE INDEX ix_channel_sources_owner ON channel_sources (owner_user_id) WHERE owner_user_id IS NOT NULL;
CREATE UNIQUE INDEX uq_channel_sources_place ON channel_sources (account_id, coalesce(external_channel_id, ''), coalesce(external_user_id, '')) WHERE external_channel_id IS NOT NULL OR external_user_id IS NOT NULL;
CREATE UNIQUE INDEX uq_channel_sources_request ON channel_sources (account_id, request_id) WHERE request_id IS NOT NULL;
CREATE TABLE channel_messages (
	channel_id UUID NOT NULL,
	pod_id UUID NOT NULL,
	seq BIGINT NOT NULL,
	client_message_id VARCHAR(128),
	source_id UUID,
	target_source_id UUID,
	sender_kind VARCHAR(10) NOT NULL,
	sender_user_id UUID,
	sender_contact_id UUID,
	sender_agent_id UUID,
	on_behalf_user_id UUID,
	on_behalf_contact_id UUID,
	sender_name VARCHAR(255),
	body TEXT NOT NULL,
	parts JSONB DEFAULT '[]'::jsonb NOT NULL,
	search TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', coalesce(body, '')) || to_tsvector('simple', coalesce(body, ''))) STORED,
	members_only BOOLEAN DEFAULT false NOT NULL,
	conversation_id UUID,
	agent_run_id UUID,
	external_message_id TEXT,
	external_thread_id TEXT,
	reply_ref JSONB,
	reply_to_id UUID,
	thread_root_id UUID,
	edited_at TIMESTAMP WITH TIME ZONE,
	deleted_at TIMESTAMP WITH TIME ZONE,
	shared_at TIMESTAMP WITH TIME ZONE,
	deleted_by_user_id UUID,
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT ck_channel_messages_sender_kind CHECK (sender_kind IN ('MEMBER', 'CONTACT', 'AGENT')),
	CONSTRAINT ck_channel_messages_one_sender CHECK (num_nonnulls(sender_user_id, sender_contact_id, sender_agent_id) <= 1 AND (sender_user_id IS NULL OR sender_kind = 'MEMBER') AND (sender_contact_id IS NULL OR sender_kind = 'CONTACT') AND (sender_agent_id IS NULL OR sender_kind = 'AGENT')),
	CONSTRAINT ck_channel_messages_only_an_agent_acts_for_someone CHECK (num_nonnulls(on_behalf_user_id, on_behalf_contact_id) <= 1 AND (sender_kind = 'AGENT' OR num_nonnulls(on_behalf_user_id, on_behalf_contact_id) = 0)),
	CONSTRAINT ck_channel_messages_heard_or_carried CHECK (num_nonnulls(source_id, target_source_id) <= 1),
	FOREIGN KEY(channel_id, pod_id) REFERENCES channels (id, pod_id) ON DELETE CASCADE,
	CONSTRAINT uq_channel_messages_seq UNIQUE (channel_id, seq),
	CONSTRAINT uq_channel_messages_id_channel UNIQUE (id, channel_id),
	CONSTRAINT uq_channel_messages_id_channel_seq UNIQUE (id, channel_id, seq),
	CONSTRAINT fk_channel_messages_thread_root FOREIGN KEY(thread_root_id, channel_id) REFERENCES channel_messages (id, channel_id) ON DELETE SET NULL (thread_root_id),
	CONSTRAINT fk_channel_messages_reply_to FOREIGN KEY(reply_to_id, channel_id) REFERENCES channel_messages (id, channel_id) ON DELETE SET NULL (reply_to_id),
	CONSTRAINT fk_channel_messages_source FOREIGN KEY(source_id, channel_id) REFERENCES channel_sources (id, channel_id) ON DELETE SET NULL (source_id) DEFERRABLE INITIALLY IMMEDIATE,
	CONSTRAINT fk_channel_messages_target_source FOREIGN KEY(target_source_id, channel_id) REFERENCES channel_sources (id, channel_id) ON DELETE SET NULL (target_source_id) DEFERRABLE INITIALLY IMMEDIATE,
	FOREIGN KEY(sender_user_id) REFERENCES users (id) ON DELETE SET NULL,
	FOREIGN KEY(sender_contact_id) REFERENCES contacts (id) ON DELETE SET NULL,
	FOREIGN KEY(sender_agent_id) REFERENCES agents (id) ON DELETE SET NULL,
	FOREIGN KEY(on_behalf_user_id) REFERENCES users (id) ON DELETE SET NULL,
	FOREIGN KEY(on_behalf_contact_id) REFERENCES contacts (id) ON DELETE SET NULL,
	FOREIGN KEY(conversation_id) REFERENCES agent_conversations (id) ON DELETE SET NULL,
	FOREIGN KEY(agent_run_id) REFERENCES agent_runs (id) ON DELETE SET NULL,
	FOREIGN KEY(deleted_by_user_id) REFERENCES users (id) ON DELETE SET NULL
);
CREATE INDEX ix_channel_messages_conversation ON channel_messages (conversation_id, seq) WHERE conversation_id IS NOT NULL;
CREATE INDEX ix_channel_messages_deleted_by ON channel_messages (deleted_by_user_id) WHERE deleted_by_user_id IS NOT NULL;
CREATE INDEX ix_channel_messages_on_behalf_contact ON channel_messages (on_behalf_contact_id) WHERE on_behalf_contact_id IS NOT NULL;
CREATE INDEX ix_channel_messages_on_behalf_user ON channel_messages (on_behalf_user_id) WHERE on_behalf_user_id IS NOT NULL;
CREATE INDEX ix_channel_messages_pod_time ON channel_messages (pod_id, created_at);
CREATE INDEX ix_channel_messages_reply_to ON channel_messages (reply_to_id) WHERE reply_to_id IS NOT NULL;
CREATE INDEX ix_channel_messages_run ON channel_messages (agent_run_id) WHERE agent_run_id IS NOT NULL;
CREATE INDEX ix_channel_messages_search ON channel_messages USING gin (pod_id, channel_id, search);
CREATE INDEX ix_channel_messages_sender_agent ON channel_messages (sender_agent_id) WHERE sender_agent_id IS NOT NULL;
CREATE INDEX ix_channel_messages_sender_contact ON channel_messages (sender_contact_id) WHERE sender_contact_id IS NOT NULL;
CREATE INDEX ix_channel_messages_sender_user ON channel_messages (sender_user_id) WHERE sender_user_id IS NOT NULL;
CREATE INDEX ix_channel_messages_source ON channel_messages (source_id, seq) WHERE source_id IS NOT NULL;
CREATE INDEX ix_channel_messages_target ON channel_messages (target_source_id, seq) WHERE target_source_id IS NOT NULL;
CREATE INDEX ix_channel_messages_thread ON channel_messages (thread_root_id, seq) WHERE thread_root_id IS NOT NULL;
CREATE UNIQUE INDEX uq_channel_messages_client ON channel_messages (channel_id, client_message_id) WHERE client_message_id IS NOT NULL;
CREATE UNIQUE INDEX uq_channel_messages_external ON channel_messages (source_id, (md5(external_message_id)::uuid)) WHERE external_message_id IS NOT NULL;
CREATE TABLE channel_mentions (
	message_id UUID NOT NULL,
	channel_id UUID NOT NULL,
	pod_id UUID NOT NULL,
	seq BIGINT NOT NULL,
	user_id UUID,
	contact_id UUID,
	agent_id UUID,
	read_at TIMESTAMP WITH TIME ZONE,
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT ck_channel_mentions_one_named CHECK (num_nonnulls(user_id, contact_id, agent_id) = 1),
	CONSTRAINT fk_channel_mentions_line FOREIGN KEY(message_id, channel_id, seq) REFERENCES channel_messages (id, channel_id, seq) ON DELETE CASCADE ON UPDATE CASCADE,
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE,
	FOREIGN KEY(contact_id) REFERENCES contacts (id) ON DELETE CASCADE,
	FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE CASCADE
);
CREATE INDEX ix_channel_mentions_agent ON channel_mentions (agent_id) WHERE agent_id IS NOT NULL;
CREATE INDEX ix_channel_mentions_contact ON channel_mentions (contact_id) WHERE contact_id IS NOT NULL;
CREATE INDEX ix_channel_mentions_user ON channel_mentions (user_id, pod_id, channel_id, seq) WHERE user_id IS NOT NULL AND read_at IS NULL;
CREATE UNIQUE INDEX uq_channel_mentions_named ON channel_mentions (message_id, coalesce(user_id, contact_id, agent_id));
CREATE TABLE channel_questions (
	message_id UUID NOT NULL,
	channel_id UUID NOT NULL,
	pod_id UUID NOT NULL,
	ref TEXT NOT NULL,
	asker VARCHAR(10) NOT NULL,
	asker_conversation_id UUID,
	asker_workflow_run_id UUID,
	asker_key TEXT NOT NULL,
	awaits_policy VARCHAR(10) DEFAULT 'ONE' NOT NULL,
	batch_id VARCHAR(255),
	kind VARCHAR(10) NOT NULL,
	state VARCHAR(10) DEFAULT 'OPEN' NOT NULL,
	question JSONB NOT NULL,
	awaits_user_id UUID,
	awaits_contact_id UUID,
	answer JSONB,
	answered_by_user_id UUID,
	answered_by_contact_id UUID,
	answered_at TIMESTAMP WITH TIME ZONE,
	expires_at TIMESTAMP WITH TIME ZONE,
	claimed_at TIMESTAMP WITH TIME ZONE,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (message_id),
	CONSTRAINT ck_channel_questions_kind CHECK (kind IN ('ASK', 'APPROVAL', 'FORM')),
	CONSTRAINT ck_channel_questions_state CHECK (state IN ('OPEN', 'ANSWERING', 'ANSWERED', 'CANCELLED', 'EXPIRED')),
	CONSTRAINT ck_channel_questions_awaits_one CHECK (num_nonnulls(awaits_user_id, awaits_contact_id) = 1),
	CONSTRAINT ck_channel_questions_asker CHECK (asker IN ('TOOL', 'WORKFLOW', 'MESSAGE')),
	CONSTRAINT ck_channel_questions_asker_ids CHECK ((asker_conversation_id IS NULL OR asker IN ('TOOL', 'MESSAGE')) AND (asker_workflow_run_id IS NULL OR asker = 'WORKFLOW')),
	CONSTRAINT ck_channel_questions_awaits_policy CHECK (awaits_policy IN ('ONE')),
	CONSTRAINT fk_channel_questions_line FOREIGN KEY(message_id, channel_id) REFERENCES channel_messages (id, channel_id) ON DELETE CASCADE ON UPDATE CASCADE,
	CONSTRAINT uq_channel_questions_ref UNIQUE (pod_id, ref),
	FOREIGN KEY(asker_conversation_id) REFERENCES agent_conversations (id) ON DELETE SET NULL,
	FOREIGN KEY(asker_workflow_run_id) REFERENCES workflow_flow_runs (id) ON DELETE SET NULL,
	FOREIGN KEY(awaits_user_id) REFERENCES users (id) ON DELETE CASCADE,
	FOREIGN KEY(awaits_contact_id) REFERENCES contacts (id) ON DELETE CASCADE,
	FOREIGN KEY(answered_by_user_id) REFERENCES users (id) ON DELETE SET NULL,
	FOREIGN KEY(answered_by_contact_id) REFERENCES contacts (id) ON DELETE SET NULL
);
CREATE INDEX ix_channel_questions_answered_contact ON channel_questions (answered_by_contact_id) WHERE answered_by_contact_id IS NOT NULL;
CREATE INDEX ix_channel_questions_answered_user ON channel_questions (answered_by_user_id) WHERE answered_by_user_id IS NOT NULL;
CREATE INDEX ix_channel_questions_asker_conversation ON channel_questions (asker_conversation_id, created_at) WHERE asker_conversation_id IS NOT NULL;
CREATE INDEX ix_channel_questions_asker_run ON channel_questions (asker_workflow_run_id) WHERE asker_workflow_run_id IS NOT NULL;
CREATE INDEX ix_channel_questions_awaits_contact ON channel_questions (awaits_contact_id) WHERE awaits_contact_id IS NOT NULL;
CREATE INDEX ix_channel_questions_awaits_user ON channel_questions (awaits_user_id) WHERE awaits_user_id IS NOT NULL;
CREATE INDEX ix_channel_questions_batch ON channel_questions (pod_id, batch_id) WHERE batch_id IS NOT NULL;
CREATE INDEX ix_channel_questions_claimed ON channel_questions (claimed_at) WHERE state = 'ANSWERING';
CREATE INDEX ix_channel_questions_expiry ON channel_questions (expires_at) WHERE state IN ('OPEN', 'ANSWERING') AND expires_at IS NOT NULL;
CREATE INDEX ix_channel_questions_orphaned ON channel_questions (created_at) WHERE state IN ('OPEN', 'ANSWERING') AND asker_conversation_id IS NULL AND asker_workflow_run_id IS NULL;
CREATE INDEX ix_channel_questions_waiting_contact ON channel_questions (awaits_contact_id) WHERE state IN ('OPEN', 'ANSWERING') AND awaits_contact_id IS NOT NULL;
CREATE INDEX ix_channel_questions_waiting_member ON channel_questions (awaits_user_id, pod_id) WHERE state IN ('OPEN', 'ANSWERING') AND awaits_user_id IS NOT NULL;
CREATE TABLE channel_message_deliveries (
	message_id UUID NOT NULL,
	channel_id UUID NOT NULL,
	pod_id UUID NOT NULL,
	seq BIGINT NOT NULL,
	source_id UUID,
	recipient_user_id UUID,
	platform VARCHAR(20),
	status VARCHAR(10) DEFAULT 'PENDING' NOT NULL,
	reason_code VARCHAR(20),
	reason TEXT,
	from_seq BIGINT,
	lines INTEGER DEFAULT 1 NOT NULL,
	urgent BOOLEAN DEFAULT false NOT NULL,
	external_message_id TEXT,
	attempts INTEGER DEFAULT 0 NOT NULL,
	next_attempt_at TIMESTAMP WITH TIME ZONE,
	claimed_at TIMESTAMP WITH TIME ZONE,
	sent_at TIMESTAMP WITH TIME ZONE,
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT ck_channel_message_deliveries_one_target CHECK (num_nonnulls(source_id, recipient_user_id) = 1),
	CONSTRAINT ck_channel_message_deliveries_status CHECK (status IN ('PENDING', 'SENT', 'FAILED', 'SKIPPED', 'DEAD')),
	CONSTRAINT ck_channel_message_deliveries_reason_code CHECK (reason_code IS NULL OR reason_code IN ('LINE_GONE', 'MEMBERS_ONLY', 'PLACE_GONE', 'NOTHING_TO_SEND', 'CANNOT_WRITE_FIRST', 'SUPPRESSED', 'NO_HANDLE', 'OUTSIDE_WINDOW', 'RATE_LIMITED', 'FORGOTTEN', 'UNREACHABLE', 'IN_APP', 'FOLDED', 'TRANSIENT', 'TIMED_OUT', 'PARTIAL', 'MAY_HAVE_ARRIVED', 'REFUSED', 'INTERRUPTED', 'UNDELIVERED')),
	CONSTRAINT ck_channel_message_deliveries_reason_when_not_sent CHECK ((status IN ('PENDING', 'SENT')) = (reason_code IS NULL)),
	CONSTRAINT ck_channel_message_deliveries_window_is_a_push CHECK (recipient_user_id IS NOT NULL OR (from_seq IS NULL AND lines = 1 AND NOT urgent)),
	FOREIGN KEY(message_id, channel_id) REFERENCES channel_messages (id, channel_id) ON DELETE CASCADE ON UPDATE CASCADE,
	FOREIGN KEY(source_id) REFERENCES channel_sources (id) ON DELETE CASCADE,
	FOREIGN KEY(recipient_user_id) REFERENCES users (id) ON DELETE CASCADE
);
CREATE INDEX ix_channel_message_deliveries_due ON channel_message_deliveries (next_attempt_at) WHERE status IN ('PENDING', 'FAILED');
CREATE INDEX ix_channel_message_deliveries_external ON channel_message_deliveries ((md5(external_message_id)::uuid)) WHERE external_message_id IS NOT NULL;
CREATE INDEX ix_channel_message_deliveries_recipient ON channel_message_deliveries (recipient_user_id) WHERE recipient_user_id IS NOT NULL;
CREATE INDEX ix_channel_message_deliveries_settled_pushes ON channel_message_deliveries (id) WHERE recipient_user_id IS NOT NULL AND status IN ('SENT', 'SKIPPED', 'DEAD');
CREATE INDEX ix_channel_message_deliveries_source ON channel_message_deliveries (source_id, (md5(external_message_id)::uuid)) WHERE source_id IS NOT NULL;
CREATE INDEX ix_cmd_due_recipient ON channel_message_deliveries (recipient_user_id, channel_id, seq) WHERE status IN ('PENDING', 'FAILED') AND recipient_user_id IS NOT NULL;
CREATE INDEX ix_cmd_due_source ON channel_message_deliveries (source_id, channel_id, seq) WHERE status IN ('PENDING', 'FAILED') AND source_id IS NOT NULL;
CREATE UNIQUE INDEX uq_channel_message_deliveries_target ON channel_message_deliveries (message_id, coalesce(source_id, recipient_user_id));
CREATE TABLE channel_delivery_attempts (
	delivery_id UUID NOT NULL,
	n INTEGER NOT NULL,
	started_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	finished_at TIMESTAMP WITH TIME ZONE,
	outcome VARCHAR(10),
	provider_status VARCHAR(32),
	error_code VARCHAR(20),
	PRIMARY KEY (delivery_id, n),
	CONSTRAINT ck_channel_delivery_attempts_outcome CHECK (outcome IS NULL OR outcome IN ('SENT', 'SKIPPED', 'FAILED', 'DEAD')),
	FOREIGN KEY(delivery_id) REFERENCES channel_message_deliveries (id) ON DELETE CASCADE
);
CREATE TABLE channel_push_pending (
	channel_id UUID NOT NULL,
	recipient_user_id UUID NOT NULL,
	pod_id UUID NOT NULL,
	from_seq BIGINT,
	to_seq BIGINT,
	lead_seq BIGINT,
	lines INTEGER DEFAULT 0 NOT NULL,
	urgent BOOLEAN DEFAULT false NOT NULL,
	next_attempt_at TIMESTAMP WITH TIME ZONE,
	pushed_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (channel_id, recipient_user_id),
	CONSTRAINT ck_channel_push_pending_window CHECK ((lines = 0) = (from_seq IS NULL) AND (from_seq IS NULL) = (to_seq IS NULL) AND (to_seq IS NULL) = (lead_seq IS NULL) AND (lead_seq IS NULL) = (next_attempt_at IS NULL)),
	FOREIGN KEY(channel_id, pod_id) REFERENCES channels (id, pod_id) ON DELETE CASCADE,
	FOREIGN KEY(recipient_user_id) REFERENCES users (id) ON DELETE CASCADE
);
CREATE INDEX ix_channel_push_pending_due ON channel_push_pending (next_attempt_at) WHERE next_attempt_at IS NOT NULL;
CREATE INDEX ix_channel_push_pending_recipient ON channel_push_pending (recipient_user_id);
CREATE TABLE channel_line_answers (
	message_id UUID NOT NULL,
	agent_id UUID NOT NULL,
	channel_id UUID NOT NULL,
	conversation_id UUID NOT NULL,
	agent_run_id UUID,
	state VARCHAR(10) DEFAULT 'ANSWERING' NOT NULL,
	answer_message_id UUID,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (message_id, agent_id),
	CONSTRAINT fk_channel_line_answers_line FOREIGN KEY(message_id, channel_id) REFERENCES channel_messages (id, channel_id) ON DELETE CASCADE ON UPDATE CASCADE,
	CONSTRAINT ck_channel_line_answers_state CHECK (state IN ('ANSWERING', 'ANSWERED', 'ENDED')),
	FOREIGN KEY(conversation_id) REFERENCES agent_conversations (id) ON DELETE CASCADE,
	FOREIGN KEY(agent_run_id) REFERENCES agent_runs (id) ON DELETE SET NULL,
	FOREIGN KEY(answer_message_id) REFERENCES channel_messages (id) ON DELETE SET NULL
);
CREATE INDEX ix_channel_line_answers_answer ON channel_line_answers (answer_message_id) WHERE answer_message_id IS NOT NULL;
CREATE INDEX ix_channel_line_answers_conversation ON channel_line_answers (conversation_id);
CREATE INDEX ix_channel_line_answers_run ON channel_line_answers (agent_run_id) WHERE agent_run_id IS NOT NULL;
CREATE TABLE channel_changes (
	channel_id UUID NOT NULL,
	rev BIGINT NOT NULL,
	message_id UUID,
	pod_id UUID NOT NULL,
	at TIMESTAMP WITH TIME ZONE DEFAULT clock_timestamp() NOT NULL,
	PRIMARY KEY (channel_id, rev),
	CONSTRAINT fk_channel_changes_channel FOREIGN KEY(channel_id, pod_id) REFERENCES channels (id, pod_id) ON DELETE CASCADE
);
CREATE INDEX ix_channel_changes_pod_at ON channel_changes (pod_id, at);
CREATE TABLE channel_threads (
	root_id UUID NOT NULL,
	channel_id UUID NOT NULL,
	reply_count INTEGER DEFAULT 0 NOT NULL,
	last_reply_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (root_id),
	CONSTRAINT fk_channel_threads_root FOREIGN KEY(root_id, channel_id) REFERENCES channel_messages (id, channel_id) ON DELETE CASCADE ON UPDATE CASCADE
);
"""

#: Storage parameters of the tables that churn, as
#: ``channels.infrastructure.storage`` sets them on the models: room on each
#: ``channels`` page for its head's next version (a HOT update), and lines and
#: deliveries vacuumed at a small fraction of the table.
_STORAGE = """
ALTER TABLE channels SET (fillfactor = 80);
ALTER TABLE channel_messages SET (autovacuum_vacuum_scale_factor = 0.02, autovacuum_vacuum_insert_scale_factor = 0.02);
ALTER TABLE channel_message_deliveries SET (autovacuum_vacuum_scale_factor = 0.02)
"""

#: Dropped in this order on a downgrade: what references what.
_NEW_TABLE_NAMES = (
    "channel_threads",
    "channel_changes",
    "channel_line_answers",
    "channel_push_pending",
    "channel_delivery_attempts",
    "channel_message_deliveries",
    "channel_questions",
    "channel_mentions",
    "channel_messages",
    "channel_sources",
    "channel_read_state",
    "channel_members",
    "channel_case_events",
    "channel_cases",
    "channels",
    "external_accounts",
)

#: ``notifications`` as it stood at 0057, for a downgrade.
_NOTIFICATIONS = """
CREATE TABLE notifications (
	pod_id UUID NOT NULL,
	recipient_user_id UUID NOT NULL,
	recipient_pod_member_id UUID NOT NULL,
	actor_user_id UUID,
	actor_agent_id UUID,
	origin_kind VARCHAR(30) NOT NULL,
	origin_id UUID,
	origin_conversation_id UUID,
	from_outside BOOLEAN DEFAULT 'false' NOT NULL,
	origin_group_title VARCHAR(255),
	asked_in_private BOOLEAN DEFAULT 'false' NOT NULL,
	asked_by_name VARCHAR(255),
	title VARCHAR(255) NOT NULL,
	body TEXT NOT NULL,
	background_instruction TEXT,
	expects_response BOOLEAN DEFAULT 'true' NOT NULL,
	action JSONB,
	status VARCHAR(30) NOT NULL,
	delivery_status VARCHAR(30) NOT NULL,
	delivery_surface_id UUID,
	delivery_conversation_id UUID,
	delivery_platform VARCHAR(50),
	delivery_error TEXT,
	response_summary TEXT,
	response_data JSONB,
	idempotency_key VARCHAR(255),
	expires_at TIMESTAMP WITH TIME ZONE,
	delivered_at TIMESTAMP WITH TIME ZONE,
	read_at TIMESTAMP WITH TIME ZONE,
	responded_at TIMESTAMP WITH TIME ZONE,
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_notifications_idempotency UNIQUE (pod_id, idempotency_key),
	FOREIGN KEY(pod_id) REFERENCES pods (id) ON DELETE CASCADE,
	FOREIGN KEY(recipient_user_id) REFERENCES users (id) ON DELETE CASCADE,
	FOREIGN KEY(recipient_pod_member_id) REFERENCES pod_members (id) ON DELETE CASCADE,
	FOREIGN KEY(actor_user_id) REFERENCES users (id) ON DELETE SET NULL,
	FOREIGN KEY(actor_agent_id) REFERENCES agents (id) ON DELETE SET NULL,
	FOREIGN KEY(origin_conversation_id) REFERENCES agent_conversations (id) ON DELETE SET NULL,
	FOREIGN KEY(delivery_surface_id) REFERENCES agent_surfaces (id) ON DELETE SET NULL,
	FOREIGN KEY(delivery_conversation_id) REFERENCES agent_conversations (id) ON DELETE SET NULL
);
CREATE INDEX ix_notifications_delivery_conversation ON notifications (delivery_conversation_id, status);
CREATE INDEX ix_notifications_delivery_status ON notifications (delivery_status);
CREATE INDEX ix_notifications_open_expires_at ON notifications (expires_at) WHERE status = 'OPEN';
CREATE INDEX ix_notifications_origin ON notifications (origin_kind, origin_id);
CREATE INDEX ix_notifications_pod_id ON notifications (pod_id);
CREATE INDEX ix_notifications_recipient_inbox ON notifications (pod_id, recipient_user_id, status, created_at);
CREATE INDEX ix_notifications_recipient_pod_member_id ON notifications (recipient_pod_member_id);
CREATE INDEX ix_notifications_recipient_user_id ON notifications (recipient_user_id);
CREATE INDEX ix_notifications_status ON notifications (status);
"""


def _run(statements: str) -> None:
    for statement in statements.split(";"):
        if statement.strip():
            op.execute(statement)


#: The tables sources replace, as they stood at 0057, for a downgrade.
_OLD_TABLES = """
CREATE TABLE agent_surface_conversation_links (
    surface_id uuid NOT NULL REFERENCES agent_surfaces(id) ON DELETE CASCADE,
    conversation_id uuid NOT NULL
        REFERENCES agent_conversations(id) ON DELETE CASCADE,
    platform character varying(50) NOT NULL,
    external_channel_id character varying(255),
    external_thread_id character varying(255) NOT NULL,
    external_user_id character varying(255),
    routed_agent_id uuid REFERENCES agents(id) ON DELETE SET NULL,
    conversation_kind character varying(50) DEFAULT 'DM' NOT NULL,
    route_key character varying(255),
    last_event jsonb NOT NULL,
    last_message_id character varying(255),
    id uuid PRIMARY KEY,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    last_inbound_at timestamp with time zone
);
CREATE INDEX ix_agent_surface_link_conversation
    ON agent_surface_conversation_links (conversation_id);
CREATE UNIQUE INDEX ix_agent_surface_link_external_thread
    ON agent_surface_conversation_links
    (surface_id, platform, external_channel_id, external_thread_id, external_user_id);
CREATE INDEX ix_agent_surface_link_external_user
    ON agent_surface_conversation_links (external_user_id, updated_at);
CREATE INDEX ix_agent_surface_link_surface_member
    ON agent_surface_conversation_links
    (surface_id, external_user_id, last_inbound_at DESC);
CREATE INDEX ix_agent_surface_link_thread_continuity
    ON agent_surface_conversation_links
    (platform, external_thread_id, external_channel_id, external_user_id,
     updated_at DESC);
CREATE TABLE agent_surface_groups (
    id uuid PRIMARY KEY,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    pod_id uuid NOT NULL REFERENCES pods(id) ON DELETE CASCADE,
    surface_id uuid NOT NULL REFERENCES agent_surfaces(id) ON DELETE CASCADE,
    platform character varying(50) NOT NULL,
    external_channel_id character varying(255),
    title character varying(255),
    owner_user_id uuid REFERENCES users(id) ON DELETE SET NULL,
    answers_outsiders boolean DEFAULT true NOT NULL,
    request_id character varying(255),
    invite_link text,
    shared_externally boolean DEFAULT false NOT NULL
);
CREATE INDEX ix_agent_surface_group_platform_channel
    ON agent_surface_groups (platform, external_channel_id);
CREATE UNIQUE INDEX ix_agent_surface_group_request
    ON agent_surface_groups (request_id) WHERE request_id IS NOT NULL;
CREATE UNIQUE INDEX ix_agent_surface_group_surface_channel
    ON agent_surface_groups (surface_id, external_channel_id);
CREATE TABLE agent_surface_group_messages (
    id uuid PRIMARY KEY,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    group_id uuid NOT NULL REFERENCES agent_surface_groups(id) ON DELETE CASCADE,
    external_message_id character varying(255),
    author_external_id character varying(255),
    author_name character varying(255),
    from_agent boolean DEFAULT false NOT NULL,
    body text NOT NULL,
    answered_name character varying(255),
    answered_from_public boolean DEFAULT false NOT NULL,
    answered_user_id uuid REFERENCES users(id) ON DELETE SET NULL
);
CREATE UNIQUE INDEX ix_agent_surface_group_message_external
    ON agent_surface_group_messages (group_id, external_message_id)
    WHERE external_message_id IS NOT NULL;
CREATE INDEX ix_agent_surface_group_message_recent
    ON agent_surface_group_messages (group_id, created_at DESC);
"""


#: Every member of every pod gets their own channel, with them in it as its
#: owner, as joining a pod makes it from now on (``channels.member_joined``):
#: so ``GET /channels/me`` and the notices that land there work for everyone
#: from the first request after the deploy. Nothing has been said in them, so
#: no read state is needed: a member with none has read up to line 0. Set-based
#: -- one statement per table, whatever the number of pods -- and idempotent.
_DIRECT_CHANNELS = """
INSERT INTO channels (id, pod_id, kind, visibility, member_user_id,
                      created_at, updated_at, last_line_at)
SELECT gen_random_uuid(), member.pod_id, 'DIRECT', 'PRIVATE', member.user_id,
       now(), now(), now()
FROM (
    SELECT DISTINCT pm.pod_id, om.user_id
    FROM pod_members pm
    JOIN organization_members om ON om.id = pm.organization_member_id
    WHERE om.user_id IS NOT NULL
) AS member
ON CONFLICT DO NOTHING;
INSERT INTO channel_members (id, channel_id, pod_id, user_id, role, notify,
                             seated_by, created_at, updated_at)
SELECT gen_random_uuid(), c.id, c.pod_id, c.member_user_id, 'owner', 'ALL',
       'CREATOR', now(), now()
FROM channels c
WHERE c.kind = 'DIRECT'
ON CONFLICT DO NOTHING
"""


def _ensure_btree_gin() -> None:
    """The search index keeps a line's pod beside its words (``btree_gin``, a
    contrib module every supported Postgres ships). Looked for before it is
    asked for: some managed Postgres refuse even a no-op CREATE EXTENSION to a
    role that is not an extension admin."""
    present = (
        op.get_bind()
        .execute(sa.text("SELECT 1 FROM pg_extension WHERE extname = 'btree_gin'"))
        .first()
    )
    if present is None:
        op.execute("CREATE EXTENSION btree_gin")


def upgrade() -> None:
    op.drop_table("agent_surface_group_messages")
    op.drop_table("agent_surface_groups")
    op.drop_table("agent_surface_conversation_links")
    op.execute("DELETE FROM work_items WHERE kind = 'notification.expire'")
    op.drop_table("notifications")
    _ensure_btree_gin()
    _run(_NEW_TABLES)
    _run(_STORAGE)
    _run(_DIRECT_CHANNELS)
    op.add_column("agent_conversations", sa.Column("channel_id", UUID(as_uuid=True)))
    # A channel of the conversation's own pod; its channel going leaves the
    # conversation in no channel, never in no pod. Checked apart from being
    # added, so the check does not hold every conversation writer behind it.
    op.execute(
        "ALTER TABLE agent_conversations ADD CONSTRAINT "
        "fk_agent_conversations_channel FOREIGN KEY (channel_id, pod_id) "
        "REFERENCES channels (id, pod_id) ON DELETE SET NULL (channel_id) "
        "NOT VALID"
    )
    op.execute(
        "ALTER TABLE agent_conversations "
        "VALIDATE CONSTRAINT fk_agent_conversations_channel"
    )
    # Concurrently, outside the transaction: conversations are written on
    # every turn, and a plain build would block those writers. A build that
    # failed on an earlier try leaves an invalid index of that name, which
    # IF NOT EXISTS would keep for good -- so drop it, then build.
    with op.get_context().autocommit_block():
        for name in (
            "ix_agent_conversations_channel_id",
            "uq_agent_conversations_channel_person",
        ):
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
        op.create_index(
            "ix_agent_conversations_channel_id",
            "agent_conversations",
            ["channel_id"],
            postgresql_concurrently=True,
        )
        # A person has one live conversation per agent in a channel.
        op.execute(
            "CREATE UNIQUE INDEX CONCURRENTLY "
            "uq_agent_conversations_channel_person ON agent_conversations "
            "(channel_id, coalesce(agent_id, pod_id), coalesce(contact_id, user_id)) "
            "WHERE channel_id IS NOT NULL AND parent_id IS NULL AND NOT is_archived"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "DROP INDEX CONCURRENTLY IF EXISTS uq_agent_conversations_channel_person"
        )
        op.drop_index(
            "ix_agent_conversations_channel_id",
            table_name="agent_conversations",
            postgresql_concurrently=True,
            if_exists=True,
        )
    op.drop_column("agent_conversations", "channel_id")
    # A channel names its current case, and the case its channel.
    op.drop_constraint("fk_channels_current_case", "channels", type_="foreignkey")
    for table in _NEW_TABLE_NAMES:
        op.drop_table(table)
    _run(_OLD_TABLES)
    _run(_NOTIFICATIONS)
