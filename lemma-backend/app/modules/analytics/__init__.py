"""Product analytics: the projection from domain events onto one vocabulary.

Reads identity, pod, datastore, function, agent, schedule, workflow, surface,
app, bundle and connector events, and emits the catalog in
``app.core.analytics``. Every one of those is another module's *published*
event, which is the one dependency direction that is always legal -- so nothing
imports analytics to be measured, and analytics owns the projection outright.

Contributes no API. The contract and the export boundary live in
``app.core.analytics``; membership of the module list is the switch, so a
deployment that leaves this module out is measured by nothing, exactly as the
registry intends.
"""
