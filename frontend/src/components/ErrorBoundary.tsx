import { Component, ErrorInfo, ReactNode } from "react";

type State = { error?: Error };

export class ErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = {};
  static getDerivedStateFromError(error: Error): State { return { error }; }
  componentDidCatch(_error: Error, _info: ErrorInfo) { /* Do not log health fields or hidden data. */ }
  render() {
    if (this.state.error) return <main><section className="result"><h1>页面暂时无法显示</h1><p>发生了未预期错误。请刷新页面；如果问题持续存在，请重新启动开发服务。</p><button className="primary" onClick={() => window.location.reload()}>刷新页面</button></section></main>;
    return this.props.children;
  }
}
