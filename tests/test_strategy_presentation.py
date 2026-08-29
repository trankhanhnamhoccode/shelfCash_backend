from app.decision_intelligence.contracts import BriefStrategyEvaluation, BriefStrategyReason
from app.decision_intelligence.strategy_presentation import present
import pytest
@pytest.mark.parametrize("code,phrase",[("LEAD_TIME","Hàng không thể về kịp"),("MOQ","mức đặt tối thiểu"),("PACK_SIZE","quy cách đóng gói"),("SUPPLIER_UNAVAILABLE","Nhà cung cấp"),("ORDER_CUTOFF","quá thời điểm"),("SUPPLIER_MAX_QUANTITY","giới hạn số lượng"),("SUPPLIER_MAX_COST","Giá trị đơn hàng"),("CAPACITY_CONSEQUENCE","giới hạn tồn kho"),("UNKNOWN_EXPIRY","hạn sử dụng"),("EXACT_SIMULATION_SAFETY_FLOOR","mức đáp ứng nhu cầu tối thiểu"),("RISK_CONSTRAINT_VIOLATION","giới hạn rủi ro")])
def test_known_codes_have_explicit_safe_templates(code,phrase):
 e=BriefStrategyEvaluation(strategy="lean",label="Tiết kiệm",status="rejected",selected=False,feasible=False,purchase_cost=None,reason_status="code_only",reasons=[BriefStrategyReason(kind="constraint_failure",code=code)])
 assert phrase in present(e).reason_messages[0]
def test_manager_presentation_uses_safe_vietnamese_formatting():
 e=BriefStrategyEvaluation(strategy="balanced",label="Cân bằng",status="feasible_not_selected",selected=False,feasible=True,purchase_cost=5000000,reason_status="verified",reasons=[BriefStrategyReason(kind="non_selection",code="HIGHER_PURCHASE_COST_THAN_SELECTED",values={"purchase_cost_delta":324000})])
 p=present(e); assert "vẫn là phương án hợp lệ" in p.headline and "324 nghìn đồng" in p.reason_messages[0]
 r=BriefStrategyEvaluation(strategy="lean",label="Tiết kiệm",status="rejected",selected=False,feasible=False,purchase_cost=5400000,reason_status="verified",reasons=[BriefStrategyReason(kind="constraint_failure",code="SERVICE_LEVEL_REQUIREMENT",values={"observed_fill_rate":.57,"required_fill_rate":.6})])
 text=" ".join([present(r).headline,present(r).summary,*present(r).reason_messages]).lower(); assert "mức đáp ứng nhu cầu" in text and "tỷ lệ lấp kho" not in text
