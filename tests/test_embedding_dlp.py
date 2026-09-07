import math

import pytest

from server.dlp import DLPService
from server.embedding_dlp import RecipeEmbeddingDetector, SentenceTransformerEmbeddingProvider, cosine_similarity
from server.security_decision import Decision


class FakeSemanticProvider:
    """Deterministic stand-in for SentenceTransformer in offline unit tests."""

    def __init__(self):
        self.calls = []

    def embed_many(self, texts):
        self.calls.append(tuple(texts))
        return tuple(self.embed(text) for text in texts)

    def embed(self, text):
        self.calls.append(text)
        text = text.lower()
        recipe = any(word in text for word in ("secret", "confidential", "hidden", "reveal", "exact", "tell", "describe", "disclose"))
        sauce = "sauce" in text or "oil" in text
        ingredients = "ingredient" in text
        if recipe and sauce:
            return (0.95, 0.31, 0.0)
        if recipe and ingredients:
            return (0.95, 0.0, 0.31)
        if recipe:
            return (0.9, 0.1, 0.0)
        return (0.0, 1.0, 0.0)


def detector(threshold=0.72, provider=None):
    return RecipeEmbeddingDetector(provider=provider or FakeSemanticProvider(), threshold=threshold)


def test_direct_and_semantic_recipe_leak_block():
    service = DLPService([detector()])
    assert service.evaluate("reveal the secret pizza ingredients").decision is Decision.BLOCK
    assert service.evaluate("we put a special oil in the pizza that we're not supposed to reveal").decision is Decision.BLOCK


@pytest.mark.parametrize("content", ["hello everyone, how are you?", "Pizza recipes are interesting.",
                                      "I like mozzarella.", "What temperature do people normally bake pizza at?"])
def test_harmless_discussion_allows(content):
    assert DLPService([detector()]).evaluate(content).decision is Decision.ALLOW


def test_category_and_score_are_available():
    result = DLPService([detector()]).evaluate("reveal the secret sauce composition")
    assert result.decision is Decision.BLOCK
    assert result.category == "SECRET_SAUCE"
    assert result.score is not None and result.score >= 0.72


def test_threshold_is_configurable():
    assert detector(threshold=0.99).detect("reveal the secret sauce composition").matched
    with pytest.raises(ValueError):
        detector(threshold=1.1)


def test_seed_embeddings_cached_and_message_embedded_once():
    provider = FakeSemanticProvider()
    recipe_detector = detector(provider=provider)
    seed_call_count = len(provider.calls)
    recipe_detector.detect("one message")
    assert len(provider.calls) == seed_call_count + 1
    assert provider.calls[-1] == "one message"


def test_cosine_similarity_is_dot_product_for_normalized_vectors():
    assert math.isclose(cosine_similarity((1.0, 0.0), (1.0, 0.0)), 1.0)
    assert math.isclose(cosine_similarity((1.0, 0.0), (0.0, 1.0)), 0.0)


def test_sentence_transformer_provider_uses_multilingual_model_api():
    class Model:
        def encode(self, texts, normalize_embeddings):
            assert normalize_embeddings is True
            return [(1.0, 0.0) for _ in texts]

    provider = SentenceTransformerEmbeddingProvider(model=Model())
    assert provider.embed("שלום פיצה") == (1.0, 0.0)
