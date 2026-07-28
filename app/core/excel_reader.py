from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

import pandas as pd

from app.schemas.llm import SheetProfile

ALLOWED_EXTENSIONS = {".xlsx", ".xls", ".xlsm", ".csv"}


class ExcelIngestionError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        details: dict | None = None,
        status_code: int = 400,
    ):
        super().__init__(message)
        self.code, self.message, self.details = code, message, details or {}
        self.status_code = status_code


@dataclass
class ParsedSheet:
    sheet_id: str
    profile: SheetProfile
    rows: list[dict[str, Any]]


def sanitize_filename(filename: str | None) -> str:
    return Path(filename or "upload.xlsx").name


def _json_value(value: Any) -> Any:
    if pd.isna(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if hasattr(value, "item"):
        return value.item()
    return value


def detect_header(raw: pd.DataFrame, scan_rows: int = 15) -> int:
    best_index, best_score = 0, -1.0
    for index in range(min(scan_rows, len(raw))):
        values = [v for v in raw.iloc[index].tolist() if not pd.isna(v) and str(v).strip()]
        if not values:
            continue
        non_empty = len(values)
        string_ratio = sum(isinstance(v, str) for v in values) / non_empty
        unique_ratio = len({str(v).strip().lower() for v in values}) / non_empty
        score = non_empty + (2 * string_ratio) + unique_ratio
        if score > best_score:
            best_index, best_score = index, score
    return best_index


def read_workbook(
    content: bytes,
    filename: str,
    max_sheets: int,
    max_rows: int,
    sample_rows: int,
) -> list[ParsedSheet]:
    safe_name = sanitize_filename(filename)
    extension = Path(safe_name).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise ExcelIngestionError("invalid_file_extension", f"Unsupported Excel extension: {extension}")
    if extension == ".csv":
        try:
            frame = pd.read_csv(BytesIO(content), dtype=object, encoding="utf-8-sig")
        except Exception as exc:
            raise ExcelIngestionError("invalid_csv_file", "Could not read CSV file") from exc
        frame = frame.dropna(how="all")
        if len(frame) > max_rows:
            raise ExcelIngestionError("too_many_rows", f"CSV exceeds {max_rows} rows")
        columns = [
            str(column).strip() if str(column).strip() else f"column_{index + 1}"
            for index, column in enumerate(frame.columns)
        ]
        frame.columns = columns
        rows = [
            {key: _json_value(value) for key, value in row.items()}
            for row in frame.to_dict(orient="records")
        ]
        profile = SheetProfile(
            file_name=safe_name,
            sheet_name=Path(safe_name).stem,
            header_row_zero_based=0,
            row_count=len(frame),
            column_count=len(columns),
            columns=columns,
            dtypes={column: str(frame[column].infer_objects().dtype) for column in columns},
            sample_rows=rows[:sample_rows],
        )
        return [ParsedSheet(f"0:{profile.sheet_name}", profile, rows)]
    try:
        book = pd.ExcelFile(BytesIO(content), engine="xlrd" if extension == ".xls" else "openpyxl")
    except Exception as exc:
        raise ExcelIngestionError("invalid_excel_file", "Could not read Excel workbook") from exc
    if len(book.sheet_names) > max_sheets:
        raise ExcelIngestionError("too_many_sheets", f"Workbook exceeds {max_sheets} sheets")

    parsed: list[ParsedSheet] = []
    for sheet_index, sheet_name in enumerate(book.sheet_names):
        raw = pd.read_excel(book, sheet_name=sheet_name, header=None, dtype=object)
        header_row = detect_header(raw)
        frame = pd.read_excel(book, sheet_name=sheet_name, header=header_row, dtype=object)
        frame = frame.dropna(how="all")
        if len(frame) > max_rows:
            raise ExcelIngestionError("too_many_rows", f"Sheet '{sheet_name}' exceeds {max_rows} rows")
        columns = [str(c).strip() if str(c).strip() else f"column_{i + 1}" for i, c in enumerate(frame.columns)]
        frame.columns = columns
        rows = [{key: _json_value(value) for key, value in row.items()} for row in frame.to_dict(orient="records")]
        profile = SheetProfile(
            file_name=safe_name,
            sheet_name=str(sheet_name),
            header_row_zero_based=header_row,
            row_count=len(frame),
            column_count=len(columns),
            columns=columns,
            dtypes={column: str(frame[column].infer_objects().dtype) for column in columns},
            sample_rows=rows[:sample_rows],
        )
        parsed.append(ParsedSheet(f"{sheet_index}:{sheet_name}", profile, rows))
    return parsed
