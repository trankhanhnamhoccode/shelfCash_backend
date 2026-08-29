"""Deterministic Vietnamese phrasing for manager-safe strategy evaluations only."""
from app.decision_intelligence.contracts import BriefStrategyEvaluation, BriefStrategyPresentation
from app.decision_intelligence.display import purchase_cost_display, vi_number
def _money(v): return (f"{vi_number(v/1000,0)} nghìn đồng" if isinstance(v,(int,float)) and 0 < v < 1000000 else purchase_cost_display(v) if isinstance(v,(int,float)) else None)
def _pct(v): return f"{vi_number(float(v)*100,2)}%" if isinstance(v,(int,float)) else None
def present(e):
 r=e.reasons; msgs=[]
 for x in r:
  v=x.values;c=x.code
  if c=="BUDGET": msgs.append(f"Chi phí nhập dự kiến là {_money(v.get('planned_cost'))}, cao hơn ngân sách {_money(v.get('budget_limit'))}." if _money(v.get('planned_cost')) and _money(v.get('budget_limit')) else "Chi phí nhập vượt giới hạn ngân sách của kế hoạch.")
  elif c=="SERVICE_LEVEL_REQUIREMENT": msgs.append(f"Mức đáp ứng nhu cầu dự kiến là {_pct(v.get('observed_fill_rate'))}, thấp hơn mức yêu cầu {_pct(v.get('required_fill_rate'))}." if _pct(v.get('observed_fill_rate')) and _pct(v.get('required_fill_rate')) else "Phương án không đạt mức đáp ứng nhu cầu yêu cầu.")
  elif c=="HIGHER_PURCHASE_COST_THAN_SELECTED": msgs.append(f"Chi phí cao hơn khoảng {_money(v.get('purchase_cost_delta'))}." if _money(v.get('purchase_cost_delta')) else "Chi phí nhập cao hơn phương án được chọn.")
  elif c=="LOWEST_EXACT_VALID_CANDIDATE_COST": msgs.append(f"Chi phí nhập dự kiến là {_money(v.get('selected_purchase_cost'))}." if _money(v.get('selected_purchase_cost')) else "Chi phí nhập thấp nhất trong các phương án hợp lệ.")
  elif c=="STRATEGY_NAME_TIEBREAK": msgs.append("Hệ thống áp dụng quy tắc phân định cố định khi chi phí bằng nhau.")
  elif c=="SELECTION_REASON_UNAVAILABLE": msgs.append("Chưa đủ dữ liệu để xác nhận chi tiết lý do.")
  elif c=="EVALUATION_TECHNICAL_FAILURE": msgs.append("Quá trình tính toán gặp lỗi kỹ thuật, nên chưa thể kết luận phương án có đáp ứng điều kiện kinh doanh hay không.")
  elif c=="LEAD_TIME": msgs.append("Hàng không thể về kịp thời điểm cần sử dụng." if not v.get('earliest_arrival_date') else f"Hàng không thể về kịp thời điểm cần sử dụng; ngày nhận sớm nhất là {v['earliest_arrival_date']}.")
  elif c=="MOQ": msgs.append("Lượng đặt hàng không đáp ứng mức đặt tối thiểu của nhà cung cấp.")
  elif c=="PACK_SIZE": msgs.append("Lượng đặt hàng không đáp ứng quy cách đóng gói bắt buộc.")
  elif c=="SUPPLIER_UNAVAILABLE": msgs.append("Nhà cung cấp không thể đáp ứng đơn hàng trong thời gian của kế hoạch.")
  elif c=="ORDER_CUTOFF": msgs.append("Đã quá thời điểm có thể đặt hàng để đáp ứng kế hoạch này.")
  elif c=="SUPPLIER_MAX_QUANTITY": msgs.append("Lượng cần đặt vượt giới hạn số lượng mà nhà cung cấp cho phép.")
  elif c=="SUPPLIER_MAX_COST": msgs.append("Giá trị đơn hàng vượt giới hạn cho phép của nhà cung cấp.")
  elif c=="CAPACITY_CONSEQUENCE": msgs.append("Lượng tồn dự kiến vượt giới hạn tồn kho được cấu hình cho nguyên liệu.")
  elif c=="UNKNOWN_EXPIRY": msgs.append("Không đủ thông tin hạn sử dụng để xác nhận phương án này đáp ứng yêu cầu.")
  elif c=="EXACT_SIMULATION_SAFETY_FLOOR": msgs.append("Phương án không đạt mức đáp ứng nhu cầu tối thiểu sau khi kế hoạch được kiểm tra lại.")
  elif c=="RISK_CONSTRAINT_VIOLATION": msgs.append("Phương án không đáp ứng giới hạn rủi ro đã cấu hình.")
  else: msgs.append("Phương án không đáp ứng một điều kiện bắt buộc của kế hoạch.")
 if e.status=="selected": h=f"{e.label} {'là phương án được chọn' if e.reason_status=='unavailable' else 'được chọn'}"; s="Chưa đủ dữ liệu để xác nhận lý do so sánh giữa các phương án." if e.reason_status=='unavailable' else "Đây là phương án hợp lệ có chi phí nhập thấp nhất trong các phương án được đánh giá."
 elif e.status=="feasible_not_selected": h=f"{e.label} vẫn là phương án hợp lệ"; s="Phương án này không được chọn vì chi phí nhập dự kiến cao hơn phương án được chọn." if any(x.code=="HIGHER_PURCHASE_COST_THAN_SELECTED" for x in r) else "Phương án này vẫn hợp lệ và có cùng chi phí nhập với phương án được chọn."
 elif e.status=="technical_failure": h=f"Chưa thể đánh giá đầy đủ phương án {e.label}";s="Quá trình tính toán gặp lỗi kỹ thuật, nên chưa thể kết luận phương án có đáp ứng điều kiện kinh doanh hay không."
 elif e.status=="not_evaluated": h=f"Chưa có đủ kết quả đánh giá cho phương án {e.label}";s="Hệ thống chưa có đủ dữ liệu để kết luận phương án này có đáp ứng các điều kiện hay không."
 else: h=f"{e.label} không đáp ứng {len(r)} điều kiện bắt buộc";s="Phương án này không được đưa vào nhóm có thể lựa chọn."
 return BriefStrategyPresentation(headline=h,summary=s,reason_messages=msgs)
def present_all(rows): return [x.model_copy(update={"presentation":present(x)}) for x in rows]
