import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./styles/tokens.css";
import "./styles/base.css";
import "./styles/planning.css";
import "./styles/result.css";
import "./styles/workspace.css";
import "./styles/history.css";
import "./styles/admin.css";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { UserProfileProvider } from "./context/UserProfileContext";
import { AuthProvider } from "./context/AuthContext";

createRoot(document.getElementById("root")!).render(<StrictMode><ErrorBoundary><AuthProvider><UserProfileProvider><App /></UserProfileProvider></AuthProvider></ErrorBoundary></StrictMode>);
