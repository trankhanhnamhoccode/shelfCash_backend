from app.core.canonical_schemas import CANONICAL_SCHEMAS
from app.core.rule_mapper import map_sheet_rules
from app.schemas.llm import SheetProfile


def test_business_constraints_mapping():
    columns = ["Loại điều kiện", "Áp dụng cho NL", "Giá trị", "Bắt đầu", "Ghi chú"]
    profile = SheetProfile(
        file_name="fake.xlsx", sheet_name="Điều kiện vận hành", header_row_zero_based=0,
        row_count=1, column_count=len(columns), columns=columns, dtypes={c: "object" for c in columns}, sample_rows=[],
    )
    result = map_sheet_rules(profile)
    assert result.sheet_type == "business_constraints"
    assert result.column_mapping == {
        "Loại điều kiện": "constraint_type", "Áp dụng cho NL": "ingredient_name",
        "Giá trị": "value", "Bắt đầu": "effective_date", "Ghi chú": "note",
    }
    assert all(value in CANONICAL_SCHEMAS[result.sheet_type]["fields"] for value in result.column_mapping.values() if value)
