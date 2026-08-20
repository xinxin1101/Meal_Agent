import { fireEvent, render, screen } from "@testing-library/react";
import { vi } from "vitest";
import { ConfirmDialog } from "../components/ui/ConfirmDialog";

test("destructive confirmation defaults focus to cancel and requires explicit confirmation", () => {
  const confirm = vi.fn();
  const cancel = vi.fn();
  render(<ConfirmDialog open title="删除记录？" description="删除后无法恢复。" onCancel={cancel} onConfirm={confirm}/>);
  expect(screen.getByRole("button", { name: "取消" })).toHaveFocus();
  expect(confirm).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "确认删除" }));
  expect(confirm).toHaveBeenCalledTimes(1);
});
