"""One plugin per provider whose webhooks this deployment receives.

The plugins live in `connectors` because that is what they are made of: Composio
verifies through its own SDK, GitHub through the App's webhook secret, and both
secrets belong to a connector account. They satisfy `schedule`'s
`WebhookSourcePlugin` port, so they are published as a factory from
`connectors/contracts/webhook_sources.py` rather than imported directly.
"""
