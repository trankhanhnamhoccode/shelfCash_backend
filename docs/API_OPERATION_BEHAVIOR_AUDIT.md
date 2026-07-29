# API Operation Behavior Audit

Audited against `ShelfCash_API_Contract_v1(3).md`. Evidence is the route-specific
tests plus `tests/test_api_contract_routes.py` and `tests/test_completion_behavior.py`.
Forecast and planning persist blocked runs when their production engines/artifacts
are unavailable; they never emit fabricated points or recommendations.

| Method | Path | Status | Evidence |
|---|---|---|---|
| GET | /health | production_complete | test_api |
| GET | /api/v1/llm/health | production_complete | test_api |
| POST | /api/v1/llm/map-sheet | production_complete | test_api |
| POST | /api/v1/imports | production_complete | test_import_persistence |
| GET | /api/v1/imports/{import_id} | production_complete | test_import_persistence |
| POST | /api/v1/imports/{import_id}/confirm | production_complete | test_import_persistence |
| POST | /api/v1/imports/{import_id}/process | production_complete | test_business_persistence |
| GET | /api/v1/imports/{import_id}/result | production_complete | test_import_persistence |
| GET | /api/v1/stores/{store_id}/imports | production_complete | operational_service |
| GET | /api/v1/stores/{store_id}/bootstrap | production_complete | completion_service |
| GET | /api/v1/stores/{store_id}/dashboard | production_complete | completion_service |
| GET | /api/v1/stores/{store_id}/inventory | production_complete | test_completion_behavior |
| POST | /api/v1/stores/{store_id}/inventory-counts | production_complete | completion_service |
| POST | /api/v1/stores/{store_id}/inventory-adjustments | production_complete | completion_service |
| GET | /api/v1/stores/{store_id}/inventory-movements | production_complete | operational_service |
| GET | /api/v1/stores/{store_id}/ingredients | production_complete | test_catalog_recipe_api |
| POST | /api/v1/stores/{store_id}/ingredients | production_complete | test_catalog_recipe_api |
| PATCH | /api/v1/stores/{store_id}/ingredients/{ingredient_id} | production_complete | test_catalog_recipe_api |
| GET | /api/v1/stores/{store_id}/products | production_complete | test_catalog_recipe_api |
| POST | /api/v1/stores/{store_id}/products | production_complete | test_catalog_recipe_api |
| PATCH | /api/v1/stores/{store_id}/products/{product_id} | production_complete | test_catalog_recipe_api |
| GET | /api/v1/stores/{store_id}/products/{product_id}/recipe | production_complete | test_catalog_recipe_api |
| PUT | /api/v1/stores/{store_id}/products/{product_id}/recipe | production_complete | test_catalog_recipe_api |
| GET | /api/v1/stores/{store_id}/products/{product_id}/recipe-versions | production_complete | operational_service |
| GET | /api/v1/stores/{store_id}/sales-history | production_complete | operational_service |
| GET | /api/v1/stores/{store_id}/usage-history | production_complete | operational_service |
| GET | /api/v1/stores/{store_id}/purchase-history | production_complete | operational_service |
| POST | /api/v1/stores/{store_id}/sales-history/batch | production_complete | test_completion_behavior |
| POST | /api/v1/stores/{store_id}/purchase-history/batch | production_complete | test_completion_behavior |
| GET | /api/v1/stores/{store_id}/supplier-constraints | production_complete | test_completion_behavior |
| POST | /api/v1/stores/{store_id}/supplier-constraints | production_complete | test_completion_behavior |
| PUT | /api/v1/stores/{store_id}/supplier-constraints/{constraint_id} | production_complete | test_completion_behavior |
| GET | /api/v1/stores/{store_id}/aliases | production_complete | test_catalog_recipe_api |
| PUT | /api/v1/stores/{store_id}/aliases | production_complete | test_catalog_recipe_api |
| GET | /api/v1/stores/{store_id}/settings | production_complete | test_completion_behavior |
| PUT | /api/v1/stores/{store_id}/settings | production_complete | test_completion_behavior |
| GET | /api/v1/stores/{store_id}/calendar-features | production_complete | test_completion_behavior |
| PUT | /api/v1/stores/{store_id}/calendar-features | production_complete | test_completion_behavior |
| POST | /api/v1/stores/{store_id}/forecast-runs | model_blocked | persisted MODEL_NOT_READY path |
| GET | /api/v1/stores/{store_id}/forecast-runs/{forecast_run_id} | model_blocked | persisted blocked status |
| GET | /api/v1/stores/{store_id}/forecast-runs/{forecast_run_id}/result | model_blocked | no fabricated points |
| POST | /api/v1/stores/{store_id}/plan-runs | model_blocked | persisted MODEL_NOT_READY path |
| GET | /api/v1/stores/{store_id}/plan-runs/{plan_run_id} | model_blocked | persisted blocked status |
| GET | /api/v1/stores/{store_id}/plan-runs/{plan_run_id}/result | model_blocked | no fabricated recommendations |
| POST | /api/v1/stores/{store_id}/purchase-orders | production_complete | test_completion_behavior |
| GET | /api/v1/stores/{store_id}/purchase-orders | production_complete | completion_service |
| GET | /api/v1/stores/{store_id}/purchase-orders/{po_id} | production_complete | completion_service |
| PATCH | /api/v1/stores/{store_id}/purchase-orders/{po_id} | production_complete | completion_service |
| POST | /api/v1/stores/{store_id}/purchase-orders/{po_id}/confirm | production_complete | test_completion_behavior |
| POST | /api/v1/stores/{store_id}/purchase-orders/{po_id}/receive | production_complete | test_completion_behavior |

Summary: `production_complete_count=44`, `model_blocked_count=6`,
`incomplete_count=0`.
