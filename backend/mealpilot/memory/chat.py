from mealpilot.domain.models import AdoptedMealPlan, ChatPlanContext, ChatResponse, ConversationMessage, HistoryPlanReference, PreferenceMemory
from mealpilot.llm.siliconflow import load_siliconflow_settings


def answer(message: str, memory: PreferenceMemory, plan_context: ChatPlanContext | None = None, history: list[AdoptedMealPlan] | None = None, prior_messages: list[ConversationMessage] | None = None) -> ChatResponse:
    """Never infer or store health data; LLM is optional and receives only explicit preferences."""
    settings = load_siliconflow_settings()
    preferences = "; ".join(f"{item.category}: {item.value}" for item in memory.items)
    plan_summary = "No active verified plan." if plan_context is None else f"Active verified plan: {'; '.join(plan_context.meals)}. Totals: {plan_context.totals.model_dump_json()}."
    history = (history or [])[:10]
    history_summary = "No adopted plan history." if not history else "Adopted plan history: " + " | ".join(
        f"{item.history_id} at {item.adopted_at.date()}: "
        + "; ".join(f"{meal.slot}={meal.recipe_title}" for meal in item.meals)
        + f"; totals={item.verified_totals.model_dump_json()}"
        for item in history
    )
    references = [HistoryPlanReference(history_id=item.history_id, original_plan_id=item.original_plan_id, adopted_at=item.adopted_at) for item in history]
    recent_dialogue = (prior_messages or [])[-10:]
    if settings.configured and settings.multi_agent_enabled:
        from openai import OpenAI
        try:
            client = OpenAI(api_key=settings.api_key, base_url=settings.base_url, timeout=10, max_retries=0)
            dialogue_messages = [
                {"role": item.role, "content": item.content}
                for item in recent_dialogue
            ]
            result = client.chat.completions.create(model=settings.model, temperature=0.2, max_tokens=260, messages=[
                {"role": "system", "content": "You are a non-medical meal-planning assistant. Use only the explicit preferences, active verified plan, and user-adopted history supplied below. Adoption is a historical fact, not proof of liking. Do not infer allergies, health conditions, or preferences; do not give medical advice or alter validated plans. Answer concisely in Chinese."},
                {"role": "system", "content": f"Saved preferences: {preferences or 'none'}. {plan_summary} {history_summary}"},
                *dialogue_messages,
                {"role": "user", "content": message},
            ])
            if result.choices[0].message.content:
                return ChatResponse(reply=result.choices[0].message.content, memories_used=memory.items, response_source="siliconflow", current_plan_used=plan_context.plan_id if plan_context else None, history_plans_used=references)
        except Exception:
            pass
    context = "；".join(item.value for item in memory.items) or "尚未保存偏好"
    history_note = ""
    if history:
        all_meals = [meal for item in history for meal in item.meals]
        repeated = sorted({meal.recipe_title for meal in all_meals if sum(other.recipe_id == meal.recipe_id for other in all_meals) > 1})
        highest = max(history, key=lambda item: (item.verified_totals.protein_g, item.adopted_at))
        if "蛋白" in message and ("最高" in message or "最多" in message):
            history_note = f"蛋白质最高的是 {highest.adopted_at.date()} 采用的计划，共 {highest.verified_totals.protein_g} g。"
        elif "重复" in message or "连续" in message:
            history_note = f"近期重复菜品：{'、'.join(repeated) if repeated else '没有发现重复菜品'}。"
        else:
            history_note = f"我已参考最近 {len(history)} 份由你明确采用的历史计划；采用记录只作为历史事实，不自动代表喜好。"
    if plan_context is not None:
        meals = "；".join(plan_context.meals)
        return ChatResponse(reply=f"我会参考你保存的偏好：{context}，以及当前已验证计划：{meals}。{history_note} 关于“{message}”，需要改变餐次时仍会重新生成并校验。", memories_used=memory.items, response_source="deterministic_fallback", current_plan_used=plan_context.plan_id, history_plans_used=references)
    return ChatResponse(reply=f"我会优先参考你保存的偏好：{context}。{history_note or '目前还没有可引用的当前计划或已采用历史。'} 你可以先生成一日计划，或继续询问历史统计。", memories_used=memory.items, response_source="deterministic_fallback", history_plans_used=references)
