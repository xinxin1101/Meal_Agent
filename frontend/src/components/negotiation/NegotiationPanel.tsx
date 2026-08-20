import { useEffect, useState } from "react";
import type { NegotiationOption, NegotiationProposal } from "../../api/types";

const fieldLabels: Record<NegotiationOption["field"], string> = {
  max_total_minutes: "总烹饪时间",
  protein_min_g: "蛋白质下限",
  energy_kcal_range: "能量范围",
};

function describe(option: NegotiationOption): string {
  if (option.field === "max_total_minutes") return `调整为 ${option.proposed_value} 分钟`;
  if (option.field === "protein_min_g") return `调整为 ${option.proposed_value} g`;
  return `调整为 ${option.proposed_value} kcal`;
}

function impact(option: NegotiationOption): string {
  if (option.field === "protein_min_g" || option.field === "energy_kcal_range") return "会放宽本次营养目标；过敏原和忌口不会改变。";
  return "增加本次可用资源；过敏原和忌口不会改变。";
}

export function NegotiationPanel({ proposal, runVersion, currentValues, pending, conflictMessage, onConfirm }: { proposal: NegotiationProposal; runVersion: number; currentValues?: Partial<Record<NegotiationOption["field"], string>>; pending: boolean; conflictMessage?: string; onConfirm: (optionId: string) => Promise<void> }) {
  const [selected, setSelected] = useState(proposal.options[0]?.option_id ?? "");
  useEffect(() => setSelected(proposal.options[0]?.option_id ?? ""), [proposal.proposal_id]);
  return <section className="negotiation" aria-labelledby="negotiation-title">
    <div className="negotiation-heading"><span>需要确认</span><div><h3 id="negotiation-title">当前条件无法同时满足</h3><p>安全约束没有被放宽。下面只列出已经由求解器验证可行的调整。</p></div></div>
    <div className="negotiation-options">{proposal.options.map((option) => <label className={selected === option.option_id ? "selected" : ""} key={option.option_id}>
      <input type="radio" name="negotiation-option" value={option.option_id} checked={selected === option.option_id} onChange={() => setSelected(option.option_id)} disabled={pending} />
      <span><b>{fieldLabels[option.field]}</b><strong>{currentValues?.[option.field] ? `${currentValues[option.field]} → ${describe(option).replace("调整为 ", "")}` : describe(option)}</strong><small>{impact(option)}</small></span>
    </label>)}</div>
    {conflictMessage && <p className="notice error">{conflictMessage}</p>}
    <div className="negotiation-actions"><small>安全约束保持不变 · 运行版本 {runVersion}</small><button className="button button-primary" type="button" disabled={!selected || pending} onClick={() => void onConfirm(selected)}>{pending ? "正在恢复任务…" : "采用调整并继续"}</button></div>
  </section>;
}
