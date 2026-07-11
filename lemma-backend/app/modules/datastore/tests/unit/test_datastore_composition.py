from uuid import uuid4

from app.modules.datastore.composition import DatastoreComposition
from app.modules.test_support.embeddings import DeterministicTestEmbedder


def test_composition_injects_embedder_into_search_adapter():
    embedder = DeterministicTestEmbedder(8)
    composition = DatastoreComposition(embedder_provider=lambda: embedder)

    service = composition.build_search_service(uuid4())

    assert service.embedder is embedder


def test_production_factory_has_no_test_adapter_selection():
    from app.core.embeddings import factory

    source = factory.__loader__.get_source(factory.__name__)
    assert source is not None
    assert "DeterministicTestEmbedder" not in source
    assert "e2e" not in source.lower()
