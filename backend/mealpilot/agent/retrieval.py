import math
import re
from collections import Counter

from mealpilot.domain.models import MealSlot, Recipe


def _tokens(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9-]+", text.casefold())
    cjk = [character for character in text if "\u4e00" <= character <= "\u9fff"]
    return words + cjk


def retrieve_recipes(query: str, recipes: list[Recipe], top_k_per_slot: int, history_recipe_counts: dict[str, int] | None = None, explicit_feedback_scores: dict[str, int] | None = None) -> list[Recipe]:
    """Small local BM25 retriever; hard filtering must happen before this function."""
    documents = {recipe.recipe_id: _tokens(" ".join([recipe.title, *(ingredient.canonical_name + " " + ingredient.canonical_id for ingredient in recipe.ingredients)])) for recipe in recipes}
    query_tokens = _tokens(query)
    history_recipe_counts = history_recipe_counts or {}
    explicit_feedback_scores = explicit_feedback_scores or {}
    if not query_tokens:
        return sorted(recipes, key=lambda item: (explicit_feedback_scores.get(item.recipe_id, 0), history_recipe_counts.get(item.recipe_id, 0), item.recipe_id))
    document_frequency = Counter(token for tokens in documents.values() for token in set(tokens))
    average_length = sum(len(tokens) for tokens in documents.values()) / max(len(documents), 1)

    def score(recipe: Recipe) -> float:
        tokens = documents[recipe.recipe_id]
        counts = Counter(tokens)
        total = 0.0
        for token in query_tokens:
            if token not in counts:
                continue
            idf = math.log(1 + (len(documents) - document_frequency[token] + 0.5) / (document_frequency[token] + 0.5))
            total += idf * (counts[token] * 2.0) / (counts[token] + 1.0 + 1.0 * len(tokens) / average_length)
        return total

    scores = {recipe.recipe_id: score(recipe) for recipe in recipes}
    selected: dict[str, Recipe] = {}
    for slot in MealSlot:
        compatible = [recipe for recipe in recipes if slot in recipe.supported_slots]
        for recipe in sorted(compatible, key=lambda item: (-scores[item.recipe_id], explicit_feedback_scores.get(item.recipe_id, 0), history_recipe_counts.get(item.recipe_id, 0), item.recipe_id))[:top_k_per_slot]:
            selected[recipe.recipe_id] = recipe
    return sorted(selected.values(), key=lambda item: item.recipe_id)
