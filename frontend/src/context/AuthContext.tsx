import { createContext, type FormEvent, type ReactNode, useContext, useEffect, useState } from "react";
import { API_BASE, apiRequest, setAccessToken } from "../api/client";
import type { AccountSummary, AuthSession } from "../api/types";
import { toUserMessage } from "../api/errors";

type AuthContextValue = {
  account: AccountSummary;
  logout: () => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | null>(null);
let bootstrapSession: Promise<AuthSession | undefined> | undefined;

function restoreSession(): Promise<AuthSession | undefined> {
  if (!bootstrapSession) bootstrapSession = fetch(`${API_BASE}/v1/auth/refresh`, { method: "POST", credentials: "include" })
    .then(async (response) => response.ok ? await response.json() as AuthSession : undefined)
    .catch(() => undefined);
  return bootstrapSession;
}

function AuthForm({ onAuthenticated }: { onAuthenticated: (session: AuthSession) => void }) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string>();
  async function submit(event: FormEvent) {
    event.preventDefault(); setPending(true); setError(undefined);
    try {
      const session = await apiRequest<AuthSession>(`/v1/auth/${mode}`, { method: "POST", body: JSON.stringify(mode === "login" ? { email, password } : { email, password, display_name: displayName }) });
      onAuthenticated(session);
    } catch (requestError) { setError(toUserMessage(requestError)); }
    finally { setPending(false); }
  }
  return <main className="auth-page">
    <section className="auth-card card">
      <div className="auth-brand"><span className="brand-mark">M</span><div><strong>MealPilot</strong><small>可验证的一日膳食规划</small></div></div>
      <div><p className="eyebrow">{mode === "login" ? "欢迎回来" : "创建个人空间"}</p><h1>{mode === "login" ? "登录 MealPilot" : "注册 MealPilot"}</h1><p>你的档案、对话和采用过的计划只会归属于此账户。</p></div>
      <form onSubmit={submit} className="auth-form">
        {mode === "register" && <label><span>显示名称</span><input autoComplete="name" required maxLength={80} value={displayName} onChange={(event) => setDisplayName(event.target.value)} /></label>}
        <label><span>{mode === "login" ? "邮箱或管理员账号" : "邮箱"}</span><input type={mode === "login" ? "text" : "email"} autoComplete="username" required value={email} onChange={(event) => setEmail(event.target.value)} /></label>
        <label><span>密码</span><input type="password" autoComplete={mode === "login" ? "current-password" : "new-password"} required minLength={mode === "register" ? 12 : 1} value={password} onChange={(event) => setPassword(event.target.value)} /></label>
        {mode === "register" && <p className="field-hint">至少 12 个字符。MealPilot 不会在日志中记录密码。</p>}
        {error && <div className="notice error" role="alert">{error}</div>}
        <button className="button button-primary" disabled={pending}>{pending ? "请稍候…" : mode === "login" ? "登录" : "注册并登录"}</button>
      </form>
      <button className="auth-switch" type="button" onClick={() => { setMode(mode === "login" ? "register" : "login"); setError(undefined); }}>{mode === "login" ? "还没有账户？创建账户" : "已有账户？返回登录"}</button>
      <p className="auth-safety">仅适用于健康成年人，不构成医疗建议。</p>
    </section>
  </main>;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [account, setAccount] = useState<AccountSummary>();
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    void restoreSession().then((session) => {
      if (!session) return;
      setAccessToken(session.access_token); setAccount(session.account);
    }).finally(() => setLoading(false));
  }, []);
  function authenticated(session: AuthSession) { setAccessToken(session.access_token); setAccount(session.account); }
  async function logout() { try { await apiRequest<void>("/v1/auth/logout", { method: "POST" }); } finally { setAccessToken(); setAccount(undefined); } }
  if (loading) return <main className="auth-page"><p role="status">正在恢复安全会话…</p></main>;
  if (!account) return <AuthForm onAuthenticated={authenticated}/>;
  return <AuthContext.Provider value={{ account, logout }}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside AuthProvider");
  return value;
}
