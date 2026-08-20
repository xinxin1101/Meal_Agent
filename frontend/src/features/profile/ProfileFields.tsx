import { useUserProfile } from "../../context/UserProfileContext";

const splitList = (value: string) => value.split(/[，,]/).map((item) => item.trim()).filter(Boolean);

export function ProfileFields({ radioGroupName = "allergen-status" }: { radioGroupName?: string }) {
  const { profile, allergenStatus, updateProfile, setAllergenStatus, validationErrors } = useUserProfile();
  return <div className="profile-fields">
    <section className="field-section">
      <div className="section-heading"><div><h3>规划档案</h3><p>仅用于健康成年人的一日膳食计算。</p></div></div>
      <div className="field-grid">
        <label><span>年龄</span><input value={profile.age_years} onChange={(event) => updateProfile("age_years", event.target.value)} inputMode="numeric" /></label>
        <label><span>营养计算参数</span><select value={profile.nutrition_parameter_sex} onChange={(event) => updateProfile("nutrition_parameter_sex", event.target.value as typeof profile.nutrition_parameter_sex)}><option value="unspecified">未指定</option><option value="female">女性参数</option><option value="male">男性参数</option></select></label>
        <label><span>身高（cm）</span><input value={profile.height_cm} onChange={(event) => updateProfile("height_cm", event.target.value)} inputMode="decimal" /></label>
        <label><span>体重（kg）</span><input value={profile.weight_kg} onChange={(event) => updateProfile("weight_kg", event.target.value)} inputMode="decimal" /></label>
        <label><span>活动水平</span><select value={profile.activity_level} onChange={(event) => updateProfile("activity_level", event.target.value as typeof profile.activity_level)}><option value="sedentary">久坐</option><option value="light">轻度活动</option><option value="moderate">中等活动</option><option value="active">活跃</option><option value="very_active">高强度活动</option></select></label>
        <label><span>当前目标</span><select value={profile.goal} onChange={(event) => updateProfile("goal", event.target.value as typeof profile.goal)}><option value="lose">减脂</option><option value="maintain">维持</option><option value="gain">增重</option></select></label>
      </div>
    </section>
    <section className="field-section safety-section">
      <div className="section-heading"><div><h3>过敏原安全确认</h3><p>MealPilot 不会从行为或空白字段推断过敏史。</p></div></div>
      <div className="allergen-choice" role="radiogroup" aria-label="过敏原状态">
        <label className={allergenStatus === "none" ? "selected" : ""}><input type="radio" name={radioGroupName} checked={allergenStatus === "none"} onChange={() => setAllergenStatus("none")} />已确认无已知过敏原</label>
        <label className={allergenStatus === "specified" ? "selected" : ""}><input type="radio" name={radioGroupName} checked={allergenStatus === "specified"} onChange={() => setAllergenStatus("specified")} />我有明确过敏原</label>
      </div>
      {allergenStatus === "specified" && <label className="full-field"><span>过敏原（逗号分隔）</span><input value={profile.allergens.join(", ")} onChange={(event) => updateProfile("allergens", splitList(event.target.value))} placeholder="例如：soy, egg" /></label>}
      {allergenStatus === "unconfirmed" && <p className="inline-warning">提交计划前必须完成这一项确认。</p>}
    </section>
    <section className="field-section">
      <div className="section-heading"><div><h3>主动忌口</h3><p>明确填写的忌口会作为硬过滤条件使用。</p></div></div>
      <label className="full-field"><span>主动忌口</span><input value={profile.avoidances.join(", ")} onChange={(event) => updateProfile("avoidances", splitList(event.target.value))} placeholder="例如：香菜、动物内脏" /></label>
    </section>
    {validationErrors.length > 0 && <div className="notice error" role="alert">{validationErrors.join(" ")}</div>}
  </div>;
}
