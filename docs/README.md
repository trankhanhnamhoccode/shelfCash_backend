# ShelfCash documentation

This directory contains only maintained implementation guidance. The running
OpenAPI document at `/openapi.json`, route definitions in `app/api/`, and
Pydantic contracts remain the authoritative API specification.

## API and decision assistant

- [decision_assistant_api_contract.md](decision_assistant_api_contract.md) —
  stable contract for Decision Run creation, raw package reads, `/brief`,
  `/explanation`, and `/what-if`. Frontend integrations should begin here.
- [decision-runs-api.md](decision-runs-api.md) — short operational guide and
  request examples for the unified Decision Run flow.
- [examples/decision_assistant_frontend_examples.json](examples/decision_assistant_frontend_examples.json)
  — representative frontend payloads; examples only, not a schema authority.

## Domain and persistence

Import lifecycle, request schemas, and persistence-facing responses are defined
by the import routes and generated OpenAPI schema. The repository intentionally
does not keep a second handwritten import API contract.

- [MENU_IMPORT_AND_API.md](MENU_IMPORT_AND_API.md) — menu import semantics,
  combo-product behavior, and menu read model.
- [INVENTORY_CONSTRAINT_CONTRACT.md](INVENTORY_CONSTRAINT_CONTRACT.md) —
  supplier terms, inventory/business constraints, units, and planning meaning.

## Forecast and core integration

- [FORECAST_MODEL_PREPROCESSING_SCHEMA.md](FORECAST_MODEL_PREPROCESSING_SCHEMA.md)
  — data-preprocessing contract shared by backend and Forecast Core.
- [forecast_core_payload.example.json](forecast_core_payload.example.json) —
  example Forecast Core payload; illustrative only.
- [decision-core-integration.md](decision-core-integration.md) — boundary
  between persistence adapters and the pure `shelfcash_core` computation layer.

## Documentation policy

Do not add copied OpenAPI manifests, one-off audit reports, prompt snapshots,
or frontend mock contracts here. Update the source code contract first, then
add only concise guidance that cannot be recovered from OpenAPI or code.
