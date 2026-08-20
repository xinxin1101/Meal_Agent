import { Icon } from "../../components/ui/Icon";
import { useUserProfile } from "../../context/UserProfileContext";

const activityLabels = { sedentary: "久坐", light: "轻度活动", moderate: "中等活动", active: "活跃", very_active: "高强度" };
const goalLabels = { lose: "减脂", maintain: "维持", gain: "增重" };

export function ProfileSummary({ onEdit }: { onEdit: () => void }) {
  const { profile, allergenStatus, validationErrors } = useUserProfile();
  return <aside className="profile-summary card">
    <div className="summary-title"><span><Icon name="profile" /></span><div><small>当前规划档案</small><strong>{profile.age_years} 岁 · {goalLabels[profile.goal]}</strong></div></div>
    <dl><div><dt>身体参数</dt><dd>{profile.height_cm} cm / {profile.weight_kg} kg</dd></div><div><dt>活动水平</dt><dd>{activityLabels[profile.activity_level]}</dd></div><div><dt>主动忌口</dt><dd>{profile.avoidances.join("、") || "无"}</dd></div></dl>
    <div className={`safety-state ${allergenStatus === "unconfirmed" ? "warning" : "safe"}`}><Icon name="shield" size={18} /><span>{allergenStatus === "none" ? "已确认无已知过敏原" : allergenStatus === "specified" ? `过敏原：${profile.allergens.join("、") || "待填写"}` : "过敏原信息尚未确认"}</span></div>
    <button className="button button-secondary" type="button" onClick={onEdit}><Icon name="settings" size={17} />编辑档案{validationErrors.length > 0 && <span className="error-count">{validationErrors.length}</span>}</button>
  </aside>;
}
