from app.models.business import IngredientAliasModel, IngredientModel
from app.services.decision.explanation_ingredient_resolver import ExplanationIngredientResolver


def _ingredient(ingredient_id, name, store_id="STORE_001"):
    return IngredientModel(
        ingredient_id=ingredient_id, store_id=store_id, ingredient=name,
        normalized_name=name.casefold(), base_unit="kg", active=True, source="test",
    )


def _resolver(session):
    session.add_all([
        _ingredient("banana", "Chuối"), _ingredient("mango", "Xoài"),
        _ingredient("wheat", "Bột mì"), _ingredient("rice", "Bột gạo"),
        _ingredient("saffron", "Saffron"), _ingredient("other-banana", "Banana", "STORE_002"),
    ])
    session.add_all([
        IngredientAliasModel(alias_id="banana-en", store_id="STORE_001", ingredient_id="banana", alias="banana", normalized_alias="banana"),
        IngredientAliasModel(alias_id="banana-en-other", store_id="STORE_002", ingredient_id="other-banana", alias="plantain", normalized_alias="plantain"),
    ])
    session.flush()
    return ExplanationIngredientResolver(session)


def test_canonical_alias_normalized_and_run_scope_resolution(client):
    with client.app.state.session_factory() as session:
        resolver = _resolver(session)
        universe = {"banana", "mango"}
        assert resolver.resolve(question="Tại sao mua Chuối?", store_id="STORE_001", decision_run_ingredient_ids=universe).ingredient_id == "banana"
        assert resolver.resolve(question="TẠI SAO MUA CHUOI?", store_id="STORE_001", decision_run_ingredient_ids=universe).ingredient_id == "banana"
        alias = resolver.resolve(question="Why are we buying banana?", store_id="STORE_001", decision_run_ingredient_ids=universe)
        assert (alias.status, alias.ingredient_id, alias.source) == ("resolved", "banana", "alias_exact")
        assert resolver.resolve(question="Why buy plantain?", store_id="STORE_001", decision_run_ingredient_ids=universe).status == "not_found"
        outside = resolver.resolve(question="Why buy saffron?", store_id="STORE_001", decision_run_ingredient_ids=universe)
        assert (outside.status, outside.mention) == ("not_found", "Saffron")


def test_ambiguity_and_conservative_fuzzy_are_deterministic(client):
    with client.app.state.session_factory() as session:
        resolver = _resolver(session)
        flour_universe = {"wheat", "rice", "banana", "mango"}
        ambiguous = resolver.resolve(question="Tại sao mua bột?", store_id="STORE_001", decision_run_ingredient_ids=flour_universe)
        assert ambiguous.status == "ambiguous"
        assert ambiguous.candidates == (("rice", "Bột gạo"), ("wheat", "Bột mì"))

        first = resolver.resolve(question="Tại sao mua chúi?", store_id="STORE_001", decision_run_ingredient_ids=flour_universe)
        second = resolver.resolve(question="Tại sao mua chúi?", store_id="STORE_001", decision_run_ingredient_ids=flour_universe)
        assert (first.status, first.ingredient_id, first.source) == ("resolved", "banana", "fuzzy")
        assert first == second
        assert resolver.resolve(question="Tại sao mua chum?", store_id="STORE_001", decision_run_ingredient_ids=flour_universe).status == "not_found"
        assert resolver.resolve(question="Tại sao mua su?", store_id="STORE_001", decision_run_ingredient_ids=flour_universe).status == "not_found"


def test_fuzzy_winner_margin_prevents_near_runner_up_auto_resolution(client):
    """A high lexical winner is insufficient when its runner-up is too close."""
    with client.app.state.session_factory() as session:
        resolver = _resolver(session)
        session.add(_ingredient("near-runner", "Chuoia"))
        session.flush()

        # ``chuooi`` is 0.91 similar to Chuối but 0.83 similar to Chuoia:
        # the winner clears 0.88, while the 0.08 margin does not clear 0.12.
        result = resolver.resolve(
            question="Tại sao mua chuooi?", store_id="STORE_001",
            decision_run_ingredient_ids={"banana", "near-runner"},
        )

    assert (result.status, result.source, result.mention) == ("not_found", "fuzzy", "chuooi")
