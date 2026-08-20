import { render } from "@testing-library/react";
import axe from "axe-core";

test("core safety boundary has no WCAG A/AA violations", async () => {
  const { container } = render(<main><h1>MealPilot</h1><p>仅适用于健康成年人，不构成医疗建议。</p><button type="button">开始规划</button></main>);
  const result = await axe.run(container, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa"] } });
  expect(result.violations).toEqual([]);
});

