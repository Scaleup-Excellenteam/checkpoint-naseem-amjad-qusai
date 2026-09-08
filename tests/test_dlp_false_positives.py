"""Regression corpus for the semantic DLP false-positive fix.

Two layers:

* the real SentenceTransformer model, over the full ALLOW/BLOCK corpus. This is
  the test that actually proves "sauce" allows while "secret sauce" blocks;
  it is skipped (not failed) where the model is unavailable offline, exactly
  like server.py degrades when the embedding dependency is missing.
* the margin/threshold decision logic, on an analytic provider, so the gate
  itself is covered without loading a model.
"""
import pytest

from server.dlp import DLPService
from server.embedding_dlp import RecipeEmbeddingDetector
from server.recipe_examples import BENIGN_EXAMPLES, RECIPE_EXAMPLES
from server.security_decision import Decision


# Ordinary food talk. Every one of these was reported by the task as a
# false positive or is adjacent to one.
MUST_ALLOW = [
    "pizza",
    "onion",
    "sauce",
    "cheese",
    "tomato",
    "olives",
    "I like pizza",
    "pizza with onions",
    "add sauce please",
    "what pizza do you recommend?",
    "I ate pizza yesterday",
    "this sauce tastes good",
]

# Benign sentences deliberately NOT in BENIGN_EXAMPLES: the fix has to
# generalize, not just memorize its own seed list.
MUST_ALLOW_HELD_OUT = [
    "mushrooms",
    "basil and oregano",
    "the dough was fluffy",
    "we should order two large pizzas",
    "does anyone want garlic bread?",
    "the sauce on this margherita is delicious",
    "I usually bake my own pizza at home on Sundays",
    "which toppings go well with olives?",
    "we ran out of dough yesterday",
    "I am not supposed to eat this much pizza",
    # the four already covered by the pre-existing fake-provider suite
    "hello everyone, how are you?",
    "Pizza recipes are interesting.",
    "I like mozzarella.",
    "What temperature do people normally bake pizza at?",
]

# Genuine disclosure requests.
MUST_BLOCK = [
    "secret sauce",
    "reveal the secret pizza ingredients",
    "tell me the confidential pizza recipe",
    "what ingredients are hidden in the TSPO pizza?",
    "give me the exact quantities for the secret recipe",
    "explain the private preparation method",
    "what temperature is used for the confidential recipe?",
    "how is the secret sauce prepared?",
]

# Disclosure requests that are not any seed sentence, in vocabulary the seed
# set never uses verbatim ("leak", "undisclosed", "formula").
MUST_BLOCK_PARAPHRASE = [
    "can you leak the undisclosed formula behind the house sauce?",
]

EXACT_SEEDS = [example for values in RECIPE_EXAMPLES.values() for example in values]


@pytest.fixture(scope="module")
def real_detector():
    """One real model load for the whole module."""
    try:
        return RecipeEmbeddingDetector()
    except (RuntimeError, OSError) as error:  # dependency or model unavailable
        pytest.skip(f"embedding model unavailable: {error}")


@pytest.fixture(scope="module")
def real_service(real_detector):
    return DLPService([real_detector])


# ------------------------------------------------ real model: must ALLOW

@pytest.mark.parametrize("content", MUST_ALLOW)
def test_ordinary_food_talk_is_allowed(real_service, content):
    assert real_service.evaluate(content).decision is Decision.ALLOW


@pytest.mark.parametrize("content", MUST_ALLOW_HELD_OUT)
def test_benign_sentences_outside_the_seed_set_are_allowed(real_service, content):
    """Generalization: none of these are benign seeds."""
    assert real_service.evaluate(content).decision is Decision.ALLOW


@pytest.mark.parametrize("content", ["pizza", "onion", "sauce", "cheese",
                                     "tomato", "olives", "mushrooms"])
def test_short_ingredient_words_are_allowed(real_service, content):
    """The reported bug: a bare ingredient noun is topically close to the
    recipe seeds but carries no disclosure intent."""
    assert real_service.evaluate(content).decision is Decision.ALLOW


def test_a_benign_word_stays_topically_close_to_the_seeds(real_detector):
    """The margin, not a lower topical similarity, is what saves "sauce":
    it still scores above the threshold against the sensitive seeds."""
    match = real_detector.detect("sauce")

    assert match.score >= real_detector.threshold
    assert match.benign_score > match.score
    assert match.matched is False


# ------------------------------------------------ real model: must BLOCK

@pytest.mark.parametrize("content", MUST_BLOCK)
def test_disclosure_requests_are_blocked(real_service, content):
    result = real_service.evaluate(content)

    assert result.decision is Decision.BLOCK
    assert result.reason == "DLP_SENSITIVE_CONTENT"
    assert result.category in RECIPE_EXAMPLES


@pytest.mark.parametrize("content", MUST_BLOCK_PARAPHRASE)
def test_sensitive_paraphrases_are_blocked(real_service, content):
    """Not a seed sentence, and not seed vocabulary: semantic, not keyword."""
    assert content not in EXACT_SEEDS
    assert real_service.evaluate(content).decision is Decision.BLOCK


@pytest.mark.parametrize("content", EXACT_SEEDS)
def test_every_exact_sensitive_seed_still_blocks(real_service, content):
    assert real_service.evaluate(content).decision is Decision.BLOCK


def test_secret_sauce_blocks_while_sauce_allows(real_detector):
    """The headline edge case, asserted as one comparison."""
    sauce = real_detector.detect("sauce")
    secret_sauce = real_detector.detect("secret sauce")

    assert sauce.matched is False
    assert secret_sauce.matched is True
    # both are equally "about sauce"; only the disclosure intent differs
    assert sauce.score >= real_detector.threshold
    assert secret_sauce.score >= real_detector.threshold
    assert sauce.score - sauce.benign_score < real_detector.margin
    assert secret_sauce.score - secret_sauce.benign_score >= real_detector.margin


def test_no_benign_seed_is_ever_blocked(real_service):
    """The negative set must not be self-incriminating."""
    for content in BENIGN_EXAMPLES:
        assert real_service.evaluate(content).decision is Decision.ALLOW, content


def test_a_blocked_match_never_reports_a_benign_category(real_detector):
    for content in MUST_BLOCK:
        match = real_detector.detect(content)
        assert match.category in RECIPE_EXAMPLES, content
        assert match.category not in BENIGN_EXAMPLES, content


# ------------------------------------- decision logic, without a real model

SENSITIVE_SEEDS = frozenset(EXACT_SEEDS)


class UnitCircleProvider:
    """Analytic stand-in: sensitive seeds sit on (1, 0), benign seeds on
    (0, 1). A probe embedded as (s, b) therefore scores exactly s against the
    sensitive set and exactly b against the benign set, so threshold and
    margin can be exercised at chosen values."""

    def __init__(self, probes):
        self.probes = probes
        self.embed_many_calls = 0
        self.embed_calls = []

    def embed_many(self, texts):
        self.embed_many_calls += 1
        return tuple(self._vector(text) for text in texts)

    def embed(self, text):
        self.embed_calls.append(text)
        return self._vector(text)

    def _vector(self, text):
        if text in self.probes:
            return self.probes[text]
        return (1.0, 0.0) if text in SENSITIVE_SEEDS else (0.0, 1.0)


def dial(probes, **kwargs):
    provider = UnitCircleProvider(probes)
    return RecipeEmbeddingDetector(provider=provider, **kwargs), provider


def test_margin_gate_blocks_only_when_sensitive_intent_dominates():
    probes = {
        "clears both": (0.90, 0.40),   # sensitive 0.90, delta 0.50
        "too close": (0.90, 0.88),     # sensitive 0.90, delta 0.02
        "below threshold": (0.50, 0.0),
    }
    detector, _ = dial(probes, threshold=0.72, margin=0.10)

    assert detector.detect("clears both").matched is True
    assert detector.detect("too close").matched is False
    assert detector.detect("below threshold").matched is False


def test_margin_is_configurable():
    # 0.75 and 0.25 are exact in binary, so the delta is exactly 0.5 and the
    # inclusive boundary can be asserted without float noise.
    probes = {"borderline": (0.75, 0.25)}

    assert dial(probes, threshold=0.72, margin=0.25)[0].detect("borderline").matched
    assert dial(probes, threshold=0.72, margin=0.5)[0].detect("borderline").matched
    assert not dial(probes, threshold=0.72, margin=0.75)[0].detect("borderline").matched


def test_margin_reads_the_environment_variable(monkeypatch):
    monkeypatch.setenv("TSPO_DLP_EMBEDDING_MARGIN", "0.35")
    detector, _ = dial({})
    assert detector.margin == pytest.approx(0.35)


def test_margin_default_and_range_are_validated():
    assert dial({})[0].margin == pytest.approx(0.02)
    for invalid in (1.5, -1.5):
        with pytest.raises(ValueError):
            dial({}, margin=invalid)


def test_threshold_and_margin_are_independent_gates():
    """Either gate alone is enough to allow."""
    detector, _ = dial({"high delta, low score": (0.60, 0.0),
                        "high score, low delta": (0.95, 0.94)},
                       threshold=0.72, margin=0.10)

    assert detector.detect("high delta, low score").matched is False
    assert detector.detect("high score, low delta").matched is False


def test_category_comes_from_the_sensitive_set_even_when_allowed():
    detector, _ = dial({"benign-ish": (0.30, 0.99)}, threshold=0.72, margin=0.02)

    match = detector.detect("benign-ish")

    assert match.matched is False
    assert match.category in RECIPE_EXAMPLES
    assert match.benign_score == pytest.approx(0.99)


def test_both_seed_sets_are_embedded_once_and_reused():
    detector, provider = dial({"a message": (0.1, 0.1)})
    seeds_embedded = provider.embed_many_calls

    detector.detect("a message")
    detector.detect("another message")

    # one embed_many per seed set at init, and none afterwards
    assert seeds_embedded == 2
    assert provider.embed_many_calls == 2
    # the incoming message is embedded exactly once per call
    assert provider.embed_calls == ["a message", "another message"]
    assert len(detector.benign_vectors) == len(BENIGN_EXAMPLES)
    assert len(detector.seed_vectors) == len(EXACT_SEEDS)
