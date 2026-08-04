from io import BytesIO
from pathlib import Path

import pandas as pd


SHEETS = {
    "POS_T7_2026": pd.DataFrame({"Ngày": ["2026-07-26"], "Tên món": ["Cà phê sữa"], "SL bán": [12], "Giá bán": [35000]}),
    "KiemKe_27-07": pd.DataFrame({"Ngày kiểm kê": ["2026-07-27"], "Nguyên liệu": ["Cà phê"], "Tồn kho": [5.5], "Đơn vị": ["kg"]}),
    "PNK tháng 7": pd.DataFrame({"Ngày nhập": ["2026-07-20"], "Nguyên liệu": ["Sữa"], "SL nhập": [24], "Đơn vị": ["hộp"]}),
    "Vendor rules": pd.DataFrame({
        "Nhà cung cấp": ["Vendor A"],
        "Nguyên liệu": ["Sữa"],
        "MOQ": [1],
        "Order UOM": ["thùng"],
        "Pack Size": [12],
        "Base UOM": ["lít"],
        "Lead time": [2],
    }),
    "Định lượng món": pd.DataFrame({"Tên món": ["Cà phê sữa"], "Nguyên liệu": ["Cà phê"], "Định lượng": [20], "Đơn vị nguyên liệu": ["g"]}),
    "Điều kiện vận hành": pd.DataFrame({"Loại điều kiện": ["maximum stock"], "Áp dụng cho NL": ["Sữa"], "Giá trị": [100], "Đơn vị": ["lít"], "Bắt đầu": ["2026-07-01"], "Ghi chú": ["Kho mát"]}),
    "Calendar + Weather": pd.DataFrame({"Ngày": ["2026-07-27"], "Cuối tuần": ["Không"], "Ngày lễ": ["Không"], "Nhiệt độ": [31], "Lượng mưa": [2.5]}),
}


def build_fake_workbook(path: Path | None = None) -> bytes:
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        for name, frame in SHEETS.items():
            frame.to_excel(writer, sheet_name=name, index=False)
    content = buffer.getvalue()
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return content
