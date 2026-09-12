from app.schemas.menu import MenuProductResponse, MenuResponse


PRODUCT_FIELDS = {
    "product_id", "store_id", "sku", "product", "item_type", "selling_unit",
    "price", "active", "status", "list_price", "discount_rate", "savings_amount",
    "currency", "components", "version", "created_at", "updated_at",
}
COMPONENT_FIELDS = {
    "bundle_line_id", "component_product_id", "component_sku", "component_product",
    "sku", "product", "quantity", "position", "price", "status",
}


def _create_single(client, sku: str, product: str, *, status: str = "active") -> dict:
    response = client.post(
        "/api/v1/stores/STORE_001/products",
        json={
            "sku": sku,
            "product": product,
            "item_type": "single",
            "selling_unit": "ly",
            "price": 30000,
            "status": status,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_catalog_product_response_dto_preserves_current_wire_shape_and_replay(client):
    endpoint = "/api/v1/stores/STORE_001/products"
    payload = {
        "sku": "DTO-SINGLE",
        "product": "DTO Single",
        "item_type": "single",
        "selling_unit": "ly",
        "price": 30000,
        "status": "active",
    }
    first = client.post(endpoint, json=payload, headers={"Idempotency-Key": "dto-single"})
    replay = client.post(endpoint, json=payload, headers={"Idempotency-Key": "dto-single"})

    assert first.status_code == replay.status_code == 201
    body = first.json()
    assert set(body) == PRODUCT_FIELDS
    assert MenuProductResponse.model_validate(body).model_dump(mode="json") == body
    assert body["components"] == []
    assert body["price"] == body["list_price"] == 30000
    assert body["discount_rate"] == 0.0
    assert body["savings_amount"] == 0
    assert body["currency"] == "VND"
    assert body["active"] is True and body["status"] == "active"
    assert body["item_type"] == "single" and body["version"] == 1

    # A fresh service response contains datetimes; an idempotency replay uses
    # the existing JSON timestamp strings stored by the service.  Both were
    # pre-existing wire forms and the response DTO must not normalise either.
    replay_body = replay.json()
    assert replay_body["product_id"] == body["product_id"]
    assert "T" in body["created_at"]
    assert " " in replay_body["created_at"]
    assert MenuProductResponse.model_validate(replay_body).model_dump(mode="json") == replay_body


def test_combo_patch_and_component_replacement_use_the_shared_product_dto(client):
    coffee = _create_single(client, "DTO-COFFEE", "DTO Coffee")
    tea = _create_single(client, "DTO-TEA", "DTO Tea")
    combo = client.post(
        "/api/v1/stores/STORE_001/products",
        json={
            "sku": "DTO-COMBO",
            "product": "DTO Combo",
            "item_type": "combo",
            "selling_unit": "combo",
            "price": 50000,
            "status": "active",
            "components": [
                {"component_product_id": coffee["product_id"], "quantity": 2},
                {"component_product_id": tea["product_id"], "quantity": 1},
            ],
        },
    )
    assert combo.status_code == 201, combo.text
    combo_body = combo.json()
    assert MenuProductResponse.model_validate(combo_body).model_dump(mode="json") == combo_body
    assert combo_body["list_price"] == 90000
    assert combo_body["savings_amount"] == 40000
    assert combo_body["discount_rate"] == round(40000 / 90000, 6)
    assert [item["position"] for item in combo_body["components"]] == [0, 1]
    assert [item["component_product_id"] for item in combo_body["components"]] == [
        coffee["product_id"], tea["product_id"],
    ]
    assert set(combo_body["components"][0]) == COMPONENT_FIELDS
    assert combo_body["components"][0]["component_sku"] == combo_body["components"][0]["sku"]
    assert combo_body["components"][0]["component_product"] == combo_body["components"][0]["product"]

    patched = client.patch(
        f"/api/v1/stores/STORE_001/products/{combo_body['product_id']}",
        json={"version": 1, "price": 51000},
    )
    assert patched.status_code == 200, patched.text
    assert MenuProductResponse.model_validate(patched.json()).model_dump(mode="json") == patched.json()
    assert patched.json()["version"] == 2

    replaced = client.put(
        f"/api/v1/stores/STORE_001/products/{combo_body['product_id']}/components",
        json={
            "version": 2,
            "components": [
                {"component_product_id": tea["product_id"], "quantity": 3},
                {"component_product_id": coffee["product_id"], "quantity": 1},
            ],
        },
    )
    assert replaced.status_code == 200, replaced.text
    replaced_body = replaced.json()
    assert MenuProductResponse.model_validate(replaced_body).model_dump(mode="json") == replaced_body
    assert replaced_body["version"] == 3
    assert [item["position"] for item in replaced_body["components"]] == [0, 1]
    assert [item["component_product_id"] for item in replaced_body["components"]] == [
        tea["product_id"], coffee["product_id"],
    ]


def test_product_list_and_menu_response_dtos_preserve_filters_summary_and_paging(client):
    active = _create_single(client, "DTO-ACTIVE", "DTO Active")
    _create_single(client, "DTO-INACTIVE", "DTO Inactive", status="inactive")

    products = client.get("/api/v1/stores/STORE_001/products", params={"active": True, "q": "Active"})
    assert products.status_code == 200, products.text
    product_body = products.json()
    assert [item["product_id"] for item in product_body] == [active["product_id"]]
    assert [MenuProductResponse.model_validate(item).model_dump(mode="json") for item in product_body] == product_body

    menu = client.get("/api/v1/stores/STORE_001/menu", params={"status": "all", "page": 1, "page_size": 1})
    assert menu.status_code == 200, menu.text
    body = menu.json()
    assert MenuResponse.model_validate(body).model_dump(mode="json") == body
    assert set(body) == {"items", "summary", "page", "page_size", "total"}
    assert body["summary"] == {
        "single_count": 2,
        "combo_count": 0,
        "active_count": 1,
        "inactive_count": 1,
    }
    assert body["page"] == 1 and body["page_size"] == 1 and body["total"] == 2
    assert len(body["items"]) == 1


def test_product_menu_openapi_uses_explicit_response_models_without_route_drift(client):
    schema = client.get("/openapi.json").json()
    refs = {
        ("/api/v1/stores/{store_id}/products", "post"): "#/components/schemas/MenuProductResponse",
        ("/api/v1/stores/{store_id}/products/{product_id}", "patch"): "#/components/schemas/MenuProductResponse",
        ("/api/v1/stores/{store_id}/products/{product_id}/components", "put"): "#/components/schemas/MenuProductResponse",
        ("/api/v1/stores/{store_id}/menu", "get"): "#/components/schemas/MenuResponse",
    }
    list_schema = schema["paths"]["/api/v1/stores/{store_id}/products"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    assert list_schema["type"] == "array"
    assert list_schema["items"] == {"$ref": "#/components/schemas/MenuProductResponse"}

    for (path, method), expected_ref in refs.items():
        content = schema["paths"][path][method]["responses"]
        success_status = "201" if method == "post" else "200"
        assert content[success_status]["content"]["application/json"]["schema"] == {"$ref": expected_ref}

    components = schema["components"]["schemas"]
    assert {"MenuComponentResponse", "MenuProductResponse", "MenuSummaryResponse", "MenuResponse"} <= set(components)
    assert len(schema["paths"]) == 58
    assert sum(1 for path in schema["paths"].values() for method in path if method in {
        "get", "post", "put", "patch", "delete", "head", "options", "trace",
    }) == 70
