"""The two pod ORM classes `agent_surfaces` joins against.

What used to be here has gone to the modules that own it: identity's `User` and
`OrganizationMember` are published as `identity/contracts/orm.py`, and
`UserRepository` -- which carried `create` and `update`, so a chat surface could
have made a user -- is now five named operations in
`identity/contracts/surfaces.py`.

`Pod` and `PodMember` stay, for now, because they are genuine joins and pod has
no `contracts/orm.py` to publish them from. Three statements need them:

- `infrastructure/repositories/surface_repository.py` joins `Pod` to filter
  surfaces to live pods and to scope a lookup to the pod's organization,
- `infrastructure/adapters/connection_owner_adapter.py` and
  `.../routing_resolution_adapter.py` join `PodMember` to `OrganizationMember`
  in one statement, which SQLAlchemy 2.0 cannot express without the class.

The next step is a `pod/contracts/orm.py` shaped like identity's, naming these
consumers and the two operations -- `select` and `join` -- they may perform.
"""

from app.modules.pod.infrastructure.models.pod_models import Pod, PodMember

__all__ = ["Pod", "PodMember"]
