"""Deterministic, catalog-assisted ingredient resolution for Explanation."""

from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Literal
import re

from sqlalchemy import select

from app.core.names import normalize_lookup_name, normalize_name
from app.models.business import IngredientAliasModel, IngredientModel


ResolutionStatus = Literal["resolved", "not_found", "ambiguous"]
_FUZZY_MIN_SCORE = 0.88
_FUZZY_MIN_MARGIN = 0.12
_FUZZY_PLAUSIBLE_SCORE = 0.70
_MISMATCH_FUZZY_MIN_SCORE = 0.95
_MISMATCH_FUZZY_MIN_MARGIN = 0.15


@dataclass(frozen=True)
class IngredientResolution:
    status: ResolutionStatus
    ingredient_id: str | None = None
    mention: str | None = None
    source: str | None = None
    candidates: tuple[tuple[str, str], ...] = ()
    fuzzy_score: float | None = None
    fuzzy_margin: float | None = None

    @property
    def mismatch_safe(self) -> bool:
        return self.source != "fuzzy" or (
            self.fuzzy_score is not None
            and self.fuzzy_margin is not None
            and self.fuzzy_score >= _MISMATCH_FUZZY_MIN_SCORE
            and self.fuzzy_margin >= _MISMATCH_FUZZY_MIN_MARGIN
        )


@dataclass(frozen=True)
class _Term:
    ingredient_id: str
    ingredient_name: str
    value: str
    source: str


class ExplanationIngredientResolver:
    """Resolve catalog names/approved aliases, then constrain them to one run."""

    def __init__(self, session):
        self._session = session

    def resolve(self, *, question: str | None, store_id: str, decision_run_ingredient_ids: set[str]) -> IngredientResolution:
        if not question or not question.strip():
            return IngredientResolution(status="not_found")
        canonical, aliases = self._terms(store_id)
        exact_question = normalize_name(question)
        lookup_question = normalize_lookup_name(question)
        for terms, query, source in (
            (canonical, exact_question, "canonical_exact"),
            (aliases, exact_question, "alias_exact"),
            (canonical, lookup_question, "canonical_normalized"),
            (aliases, lookup_question, "alias_normalized"),
        ):
            matched = [term for term in terms if _contains_phrase(query, self._term_key(term, source))]
            if matched:
                return self._matched(matched, decision_run_ingredient_ids, source)

        prefix_matches = [
            term for term in [*canonical, *aliases]
            if any(
                len(token) >= 3 and _contains_phrase(lookup_question, token)
                for token in self._term_key(term, "canonical_normalized").split()
            )
        ]
        if prefix_matches:
            return self._matched(prefix_matches, decision_run_ingredient_ids, "prefix")

        fuzzy = self._fuzzy(lookup_question, [*canonical, *aliases], decision_run_ingredient_ids)
        if fuzzy is not None:
            return fuzzy
        return IngredientResolution(status="not_found")

    def _terms(self, store_id: str) -> tuple[list[_Term], list[_Term]]:
        ingredients = list(self._session.scalars(select(IngredientModel).where(
            IngredientModel.store_id == store_id,
        ).order_by(IngredientModel.normalized_name, IngredientModel.ingredient_id)))
        names = {item.ingredient_id: item.ingredient for item in ingredients}
        canonical = [
            _Term(item.ingredient_id, item.ingredient, item.ingredient, "canonical")
            for item in ingredients
        ]
        aliases = [
            _Term(alias.ingredient_id, names[alias.ingredient_id], alias.alias, "alias")
            for alias in self._session.scalars(select(IngredientAliasModel).where(
                IngredientAliasModel.store_id == store_id,
                IngredientAliasModel.ingredient_id.in_(names),
            ).order_by(IngredientAliasModel.normalized_alias, IngredientAliasModel.alias_id))
            if alias.ingredient_id in names
        ]
        return canonical, aliases

    @staticmethod
    def _term_key(term: _Term, source: str) -> str:
        return normalize_name(term.value) if source.endswith("exact") else normalize_lookup_name(term.value)

    def _matched(self, matched: list[_Term], universe: set[str], source: str) -> IngredientResolution:
        scoped = self._candidates(matched, universe)
        mention = min((term.value for term in matched), key=lambda value: (len(value), normalize_name(value)))
        if not scoped:
            return IngredientResolution(status="not_found", mention=mention, source=source)
        if len(scoped) > 1:
            return IngredientResolution(status="ambiguous", mention=mention, source=source, candidates=scoped)
        return IngredientResolution(status="resolved", ingredient_id=scoped[0][0], mention=mention, source=source)

    @staticmethod
    def _candidates(terms: list[_Term], universe: set[str]) -> tuple[tuple[str, str], ...]:
        by_id = {
            term.ingredient_id: term.ingredient_name
            for term in terms if term.ingredient_id in universe
        }
        return tuple(sorted(by_id.items(), key=lambda item: (normalize_name(item[1]), item[0])))

    def _fuzzy(self, question: str, terms: list[_Term], universe: set[str]) -> IngredientResolution | None:
        # A short token is too collision-prone for typo recovery. Compare only
        # words, never arbitrary substrings or semantic translations.
        tokens = sorted(set(re.findall(r"[a-z0-9]+", question)))
        scores: dict[str, tuple[float, _Term, str]] = {}
        for token in tokens:
            if len(token) < 4:
                continue
            for term in terms:
                if term.ingredient_id not in universe:
                    continue
                for candidate in re.findall(r"[a-z0-9]+", normalize_lookup_name(term.value)):
                    if len(candidate) < 4:
                        continue
                    score = SequenceMatcher(None, token, candidate, autojunk=False).ratio()
                    prior = scores.get(term.ingredient_id)
                    if prior is None or score > prior[0] or (score == prior[0] and term.value < prior[1].value):
                        scores[term.ingredient_id] = (score, term, token)
        if not scores:
            return None
        ranked = sorted(scores.items(), key=lambda item: (-item[1][0], normalize_name(item[1][1].ingredient_name), item[0]))
        winner_id, (winner_score, winner, mention) = ranked[0]
        runner_up = ranked[1][1][0] if len(ranked) > 1 else 0.0
        margin = winner_score - runner_up
        if winner_score < _FUZZY_PLAUSIBLE_SCORE:
            return None
        if winner_score < _FUZZY_MIN_SCORE or margin < _FUZZY_MIN_MARGIN:
            plausible = [entry[1] for entry in ranked if entry[1][0] >= _FUZZY_MIN_SCORE]
            if len(plausible) > 1:
                return IngredientResolution(
                    status="ambiguous", mention=mention, source="fuzzy",
                    candidates=self._candidates([entry[1] for entry in plausible], universe),
                )
            return IngredientResolution(status="not_found", mention=mention, source="fuzzy")
        return IngredientResolution(
            status="resolved", ingredient_id=winner_id, mention=mention, source="fuzzy",
            fuzzy_score=winner_score, fuzzy_margin=margin,
        )


def _contains_phrase(text: str, term: str) -> bool:
    return bool(re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text))

