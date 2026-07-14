from codelore.parsers import CHUNK_REGISTRY, REGISTRY


def test_registry_extensions_have_chunk_counterparts():
    missing = set(REGISTRY) - set(CHUNK_REGISTRY)
    assert not missing, f"extensions in REGISTRY but missing from CHUNK_REGISTRY: {missing}"


def test_registry_values_are_callable():
    for ext, fn in REGISTRY.items():
        assert callable(fn), f"REGISTRY[{ext!r}] is not callable"
    for ext, fn in CHUNK_REGISTRY.items():
        assert callable(fn), f"CHUNK_REGISTRY[{ext!r}] is not callable"


def test_registry_covers_newly_added_languages():
    for ext in (".rb", ".rs", ".php", ".c", ".h", ".cpp", ".cc", ".cxx", ".hpp", ".hh"):
        assert ext in REGISTRY
        assert ext in CHUNK_REGISTRY
