"""Editing a Google provider: the editor, with the one edit its protocol needs."""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.transaction_locks import connection_released
from app.modules.agent.application.agents.edit_runtime_profile import (
    AgentRuntimeProfileEditor,
)
from app.modules.agent.application.agents.google_genai_profiles import (
    GoogleProfileEdit,
    google_profile_changes,
    verified_google_access,
)
from app.modules.agent.domain.runtime_profiles import AgentRuntimeProfile


class GoogleGenAIProfileEditor(AgentRuntimeProfileEditor):
    async def update_google_genai_profile(
        self,
        *,
        profile_id: str,
        organization_id: UUID,
        user_id: UUID,
        edit: GoogleProfileEdit,
    ) -> AgentRuntimeProfile:
        """Edit a Google provider. Its host is fixed: moving between the Gemini
        API and Vertex AI changes how it signs in, so that is a new provider.

        Whatever changes what Google is asked as -- the credentials, the
        project, the location, the default model -- is checked with Google
        before it is saved, as a new provider is.
        """
        profile = await self._load_editable(
            profile_id=profile_id,
            organization_id=organization_id,
            user_id=user_id,
        )
        assert self._service.repository is not None
        edited = google_profile_changes(profile, edit)
        if not edited.changes:
            return profile
        if edited.asks_google_differently:
            async with connection_released(self._session()):
                await verified_google_access(
                    edited.config,
                    edited.credentials,
                    model_name=str(
                        edited.changes.get(
                            "default_model_name", profile.default_model_name
                        )
                    ),
                    provider_name=str(edited.changes.get("name", profile.name)),
                )
        return await self._service.repository.update(
            profile.with_changes(**edited.changes)
        )
