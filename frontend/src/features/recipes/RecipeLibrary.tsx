import { useEffect, useState } from "react";
import { apiClient } from "../../api/client";
import { toUserMessage } from "../../api/errors";
import type { ReadableRecipe } from "../../api/types";

const slotLabel = { breakfast: "早餐", lunch: "午餐", dinner: "晚餐" } as const;

export function RecipeLibrary() {
  const [recipes, setRecipes] = useState<ReadableRecipe[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string>();

  useEffect(() => {
    void apiClient.get<ReadableRecipe[]>("/v1/recipes")
      .then(setRecipes)
      .catch((value) => setError(toUserMessage(value)))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <section className="recipe-library-state" role="status">正在加载可阅读菜谱…</section>;
  if (error) return <section className="notice error" role="alert">{error}</section>;
  if (!recipes.length) return <section className="recipe-library-state card"><strong>暂时没有可阅读菜谱</strong><p>管理员完成来源核验与步骤整理后，菜谱会出现在这里。</p></section>;

  return <section className="recipe-library-grid" aria-label="可阅读菜谱库">
    {recipes.map((recipe) => <article className="recipe-library-card card" key={`${recipe.recipe_id}:${recipe.version}`}>
      <header><div><p>{recipe.supported_slots.length ? recipe.supported_slots.map((slot) => slotLabel[slot]).join(" / ") : "餐次待补充"}</p><h2>{recipe.title}</h2></div><span>可阅读菜谱</span></header>
      <dl><div><dt>份数</dt><dd>{recipe.servings ?? "来源未注明"}</dd></div><div><dt>时间</dt><dd>{recipe.prep_minutes === null || recipe.prep_minutes === undefined ? "来源未注明" : `${recipe.prep_minutes} 分钟`}</dd></div><div><dt>食材</dt><dd>{recipe.ingredients.length} 项</dd></div></dl>
      <section><h3>食材</h3><ul>{recipe.ingredients.map((item, index) => <li key={`${recipe.recipe_id}:${index}`}><span>{item.raw_name}</span><b>{item.display_quantity}</b></li>)}</ul></section>
      <details><summary>查看制作步骤</summary><ol>{recipe.cooking_steps.map((step) => <li key={step.step_number}>{step.instruction}</li>)}</ol></details>
      {recipe.warnings.length > 0 && <p className="recipe-library-warning">提示：{recipe.warnings.join(" · ")}</p>}
      <footer><a href={recipe.source.source_url} target="_blank" rel="noreferrer">查看来源</a><span>仅供个人学习和烹饪参考</span></footer>
    </article>)}
  </section>;
}
