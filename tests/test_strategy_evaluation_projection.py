import json
from app.decision_intelligence.strategy_evaluation_projection import project_strategy_evaluations

def test_safe_public_strategy_projection():
 p={"recommended_strategy":"protected","strategy_selection":{"rule":"lowest_exact_valid_candidate_cost_then_strategy_name","selected_strategy":"protected","eligible_candidates":["balanced","protected"]},"strategies":{"lean":{"is_feasible":False,"purchase_cost":5400000,"critic":{"findings":[{"code":"BUDGET","severity":"error","evidence":{"planned_cost":5400000,"budget_limit":5000000,"offer_id":"secret-uuid"}}],"warnings":[]}},"balanced":{"is_feasible":True,"purchase_cost":5000000,"critic":{"findings":[],"warnings":[]}},"protected":{"is_feasible":True,"purchase_cost":4676000,"critic":{"findings":[],"warnings":["CAPACITY_NOT_EVALUATED","STRESS_SHORTAGE_OBSERVED"]}}}}
 rows=project_strategy_evaluations(p); data=json.dumps([x.model_dump() for x in rows])
 assert [x.status for x in rows]==["rejected","feasible_not_selected","selected"]
 assert rows[0].reasons[0].values=={"planned_cost":5400000,"budget_limit":5000000}
 assert "secret-uuid" not in data and "CAPACITY_NOT_EVALUATED" not in data and "STRESS_SHORTAGE_OBSERVED" not in data
 assert rows[2].reasons[0].code=="LOWEST_EXACT_VALID_CANDIDATE_COST"
def test_old_package_is_empty(): assert project_strategy_evaluations({})==[]
