"""SentenceTransformer-backed semantic DLP for synthetic pizza-recipe leaks."""

import os
from dataclasses import dataclass

try:
    from .recipe_examples import BENIGN_EXAMPLES, RECIPE_EXAMPLES
except ImportError:
    from recipe_examples import BENIGN_EXAMPLES, RECIPE_EXAMPLES

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
                    "sentence-transformers is required for semantic recipe DLP; install requirements-dlp.txt"
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
    # Similarity to the closest ordinary-food-talk example. Reported for
    # calibration and tests only; the wire response still carries `score`.
    benign_score: float = 0.0


class RecipeEmbeddingDetector:
    """Blocks recipe-disclosure requests without blocking ordinary food talk.

    Similarity to the recipe seeds alone is not enough to decide: the seeds are
    themselves about pizza, so a bare ingredient noun ("sauce") lands close to
    them purely by topic. The message is therefore scored against both seed
    sets, and only a message that is closer to disclosure intent than to
    ordinary food talk - by at least `margin` - is treated as a match.
    """

    def __init__(self, provider=None, threshold: float | None = None,
                 margin: float | None = None):
        self.provider = provider or SentenceTransformerEmbeddingProvider()
        configured = os.getenv("TSPO_DLP_EMBEDDING_THRESHOLD") if threshold is None else threshold
        self.threshold = float(configured) if configured is not None else 0.72
        if not 0.0 <= self.threshold <= 1.0:
            raise ValueError("embedding threshold must be between 0 and 1")
        configured_margin = os.getenv("TSPO_DLP_EMBEDDING_MARGIN") if margin is None else margin
        # Calibrated on the ALLOW/BLOCK sets: every benign case clears it by at
        # least 0.19, and the tightest genuine leak ("secret sauce") by 0.015.
        # Deliberately low - erring small keeps disclosure requests blocked.
        self.margin = float(configured_margin) if configured_margin is not None else 0.02
        if not -1.0 <= self.margin <= 1.0:
            raise ValueError("embedding margin must be between -1 and 1")
        examples = [(category, example) for category, values in RECIPE_EXAMPLES.items()
                    for example in values]
        # One model call per seed set at initialization; vectors stay in memory
        # and are never recomputed for an incoming message.
        vectors = self.provider.embed_many([example for _, example in examples])
        self.seed_vectors = tuple((category, vector) for (category, _), vector in zip(examples, vectors))
        self.benign_vectors = tuple(self.provider.embed_many(BENIGN_EXAMPLES))

    def detect(self, content: str) -> EmbeddingMatch:
        # Exactly one embedding of the incoming message, scored against both
        # cached seed sets.
        message_vector = self.provider.embed(content)
        category, score = max(
            ((category, cosine_similarity(message_vector, vector))
             for category, vector in self.seed_vectors), key=lambda item: item[1]
        )
        benign_score = max(
            (cosine_similarity(message_vector, vector)
             for vector in self.benign_vectors), default=0.0
        )
        matched = score >= self.threshold and score - benign_score >= self.margin
        # The category always names the closest *sensitive* seed, never a
        # benign one, and is reported whether or not the message matched.
        return EmbeddingMatch(matched, category, score, benign_score)

    def __call__(self, content: str) -> bool:
        return self.detect(content).matched
