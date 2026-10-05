"""Whether a table is its contacts', as the table API reads and writes it.

Split from ``datastore_schemas`` because it is one idea added to three shapes,
and kept beside them so the three cannot disagree about what it means.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ContactOwnedOnCreate(BaseModel):
    contact_owned: bool = Field(
        default=False,
        description=(
            "Rows the pod keeps about its contacts. Adds a `contact_id` column; "
            "every member sees every row, and a contact's run reads only rows "
            "naming that contact. Not combinable with `enable_rls`."
        ),
    )


class ContactOwnedOnUpdate(BaseModel):
    contact_owned: bool | None = Field(
        default=None,
        description=(
            "Make the table contact-owned, or stop it being. Enabling adds a "
            "`contact_id` column if there is none; rows without one are seen by "
            "members only. Omit to leave it unchanged."
        ),
    )


class ContactOwnedOnRead(BaseModel):
    contact_owned: bool = False
