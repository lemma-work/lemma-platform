"""Product-analytics module: consumes domain events, contributes no API.

The contract and the export boundary live in ``app.core.analytics``. This
module exists only to register the consumer's stream groups, so a deployment
that leaves the module out is measured by nothing -- membership of the module
list is the switch, exactly as the registry intends.
"""
