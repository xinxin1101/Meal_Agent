import { useState } from "react";
import { apiRequest } from "../../api/client";
import { toUserMessage } from "../../api/errors";
import { useAuth } from "../../context/AuthContext";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";

export function AccountPrivacyPanel() {
  const { account, logout } = useAuth();
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string>();
  const [pending, setPending] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  async function exportData() {
    setError(undefined);
    try {
      const data = await apiRequest<Record<string, unknown>>("/v1/account/export", {}, 30_000);
      const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
      const link = document.createElement("a"); link.href = url; link.download = `mealpilot-${account.user_id}-export.json`; link.click(); URL.revokeObjectURL(url);
    } catch (requestError) { setError(toUserMessage(requestError)); }
  }
  async function deleteAccount() {
    setPending(true); setError(undefined);
    try { await apiRequest<void>("/v1/account", { method: "DELETE", body: JSON.stringify({ password, confirmation: "DELETE MY ACCOUNT" }) }); setDeleteOpen(false); await logout(); }
    catch (requestError) { setError(toUserMessage(requestError)); }
    finally { setPending(false); }
  }
  return <section className="card account-privacy-panel"><div><p className="eyebrow">账户与隐私</p><h2>{account.display_name}</h2><p>{account.email}</p></div><div className="privacy-actions"><button className="button button-secondary" type="button" onClick={() => void exportData()}>导出我的全部数据</button></div><div className="danger-zone"><h3>注销账户</h3><p>注销会立即删除当前账户的档案、偏好、计划历史、反馈、对话、运行及登录凭据。</p><label><span>输入当前密码确认</span><input type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} /></label><button className="button button-danger" type="button" disabled={!password || pending} onClick={() => setDeleteOpen(true)}>{pending ? "正在删除…" : "永久删除账户"}</button></div>{error && <div className="notice error" role="alert">{error}</div>}<ConfirmDialog open={deleteOpen} title="永久删除整个账户？" description="档案、偏好、计划、反馈、对话、运行记录和登录凭据将全部删除。此操作无法撤销。" confirmLabel="永久删除账户" busy={pending} onCancel={() => setDeleteOpen(false)} onConfirm={() => void deleteAccount()}/></section>;
}
