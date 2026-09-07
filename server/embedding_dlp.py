"""SentenceTransformer-backed semantic DLP for synthetic pizza-recipe leaks."""

import os
from dataclasses import dataclass

try:
    from .recipe_examples import RECIPE_EXAMPLES
except ImportError:
    from recipe_examples import RECIPE_EXAMPLES

MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


def cosine_similarity(left, right) -> float:
    return float(sum(a * b for a, b in zip(left, right)))


class SentenceTransformerEmbeddingProvider:
    def __init__(self, model_name: str = MODEL_NAME, model=None):
        if model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as error:
                raise RuntimeError(
                    "sentence-transformers is required for semantic recipe DLP; install requirements.txt"
                ) from error
            model = SentenceTransformer(model_name)
        self.model = model

    def embed_many(self, texts):
        return tuple(self.model.encode(list(texts), normalize_embeddings=True))

    def embed(self, text):
        return self.embed_many([text])[0]


@dataclass(frozen=True)
class EmbeddingMatch:
    matched: bool
    category: str | None
    score: float


class RecipeEmbeddingDetector:
    def __init__(self, provider=None, threshold: float | None = None):
        self.provider = provider or SentenceTransformerEmbeddingProvider()
        configured = os.getenv("TSPO_DLP_EMBEDDING_THRESHOLD") if threshold is None else threshold
        self.threshold = float(configured) if configured is not None else 0.72
        if not 0.0 <= self.threshold <= 1.0:
            raise ValueError("embedding threshold must be between 0 and 1")
        examples = [(category, example) for category, values in RECIPE_EXAMPLES.items()
                    for example in values]
        # One model call at initialization; vectors remain in memory.
        vectors = self.provider.embed_many([example for _, example in examples])
        self.seed_vectors = tuple((category, vector) for (category, _), vector in zip(examples, vectors))

    def detect(self, content: str) -> EmbeddingMatch:
        message_vector = self.provider.embed(content)
        category, score = max(
            ((category, cosine_similarity(message_vector, vector))
             for category, vector in self.seed_vectors), key=lambda item: item[1]
        )
        return EmbeddingMatch(score >= self.threshold, category, score)

    def __call__(self, content: str) -> bool:
        return self.detect(content).matched
