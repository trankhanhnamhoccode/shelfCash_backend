CANONICAL_SCHEMAS = {
    "inventory": {
        "fields": ["snapshot_date", "ingredient_name", "batch_id", "on_hand", "unit", "received_date", "expiry_date", "expiry_tracking_mode", "supplier_name", "warehouse_name"],
        "core_fields": ["snapshot_date", "ingredient_name", "batch_id", "on_hand", "unit"],
    },
    "sales_history": {
        "fields": ["date", "product_name", "quantity_sold", "unit", "selling_price", "revenue", "is_stockout", "promotion_name"],
        "core_fields": ["date", "product_name", "quantity_sold"],
    },
    "usage_history": {
        "fields": ["date", "ingredient_name", "quantity_used", "unit", "source", "waste_quantity"],
        "core_fields": ["date", "ingredient_name", "quantity_used"],
    },
    "recipes": {
        "fields": ["product_sku", "product_name", "ingredient_name", "ingredient_quantity", "ingredient_unit", "yield_quantity", "yield_unit", "recipe_version", "effective_date"],
        "core_fields": ["product_name", "ingredient_name", "ingredient_quantity"],
        "field_metadata": {
            "recipe_version": {
                "required": False,
                "type": "positive_integer",
                "description": "Optional internal sequential version. Leave blank to let the backend assign the next version.",
            },
        },
    },
    "purchase_history": {
        "fields": ["purchase_date", "received_date", "ingredient_name", "quantity_received", "unit", "unit_price", "total_cost", "supplier_name", "expiry_date", "expiry_tracking_mode", "batch_id", "purchase_order_id", "source", "external_record_id"],
        "core_fields": ["ingredient_name", "quantity_received"],
    },
    "supplier_constraints": {
        "fields": ["supplier_name", "ingredient_name", "minimum_order_quantity", "order_unit", "package_size", "package_base_unit", "lead_time_days", "shelf_life_days", "expiry_tracking_mode", "unit_price", "available_delivery_days"],
        "core_fields": ["supplier_name", "ingredient_name", "minimum_order_quantity", "order_unit", "package_size", "package_base_unit"],
    },
    "calendar_features": {
        "fields": ["date", "is_weekend", "is_holiday", "is_store_closed", "is_promotion", "promotion_name", "temperature", "rainfall"],
        "core_fields": ["date"],
    },
    "business_constraints": {
        "fields": ["constraint_type", "ingredient_name", "value", "unit", "currency", "effective_date", "end_date", "note"],
        "core_fields": ["constraint_type", "value"],
    },
    "menu": {
        "fields": [
            "product_sku", "item_type", "product_name", "combo_components",
            "selling_unit", "list_price", "discount_rate", "selling_price",
            "savings_amount", "status", "component_product_id", "component_sku",
            "component_product_name", "component_variant", "component_quantity",
        ],
        "core_fields": [
            "product_sku", "item_type", "product_name", "selling_price",
        ],
    },
    "unknown": {"fields": [], "core_fields": []},
}

SHEET_TYPES = tuple(CANONICAL_SCHEMAS)
