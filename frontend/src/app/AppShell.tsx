import type { ReactNode } from "react";
import { Icon } from "../components/ui/Icon";
import type { AccountSummary } from "../api/types";
import { useAuth } from "../context/AuthContext";

export type AppPage = "plan" | "history" | "assistant" | "profile" | "admin-recipes";

const navItems = [
  { id: "plan", label: "今日计划", icon: "calendar" },
  { id: "history", label: "历史计划", icon: "history" },
  { id: "assistant", label: "对话助手", icon: "chat" },
  { id: "profile", label: "偏好与档案", icon: "profile" },
] as const;

const adminNavItem = { id: "admin-recipes", label: "菜谱管理", icon: "nutrition" } as const;

export function AppShell({ activePage, onNavigate, memoryCount, historyCount, account, children }: { activePage: AppPage; onNavigate: (page: AppPage) => void; memoryCount: number; historyCount: number; account: AccountSummary; children: ReactNode }) {
  const { logout } = useAuth();
  const visibleNavItems = account.role === "ADMIN" ? [...navItems, adminNavItem] : navItems;
  return <div className="app-shell">
    <header className="topbar">
      <button className="brand" type="button" onClick={() => onNavigate("plan")} aria-label="返回今日计划">
        <span className="brand-mark"><Icon name="nutrition" size={22} /></span>
        <span><strong>MealPilot</strong><small>可信的一日膳食规划</small></span>
      </button>
      <nav className="desktop-nav" aria-label="主要导航">
        {visibleNavItems.map((item) => <button key={item.id} className={activePage === item.id ? "active" : ""} type="button" onClick={() => onNavigate(item.id)} aria-current={activePage === item.id ? "page" : undefined}><Icon name={item.icon} size={18} />{item.label}{item.id === "profile" && memoryCount > 0 && <span className="nav-count">{memoryCount}</span>}{item.id === "history" && historyCount > 0 && <span className="nav-count">{historyCount}</span>}</button>)}
      </nav>
      <div className="account-menu"><span title={account.email}>{account.display_name}</span><button type="button" onClick={() => void logout()}>退出</button></div>
    </header>
    <main id="main-content" className="page-shell">{children}</main>
    <nav className="mobile-nav" aria-label="移动端主要导航">
      {visibleNavItems.map((item) => <button key={item.id} className={activePage === item.id ? "active" : ""} type="button" onClick={() => onNavigate(item.id)} aria-current={activePage === item.id ? "page" : undefined}><Icon name={item.icon} size={20} /><span>{item.label}</span></button>)}
    </nav>
  </div>;
}
