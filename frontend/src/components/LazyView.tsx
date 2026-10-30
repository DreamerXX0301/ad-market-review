import { Component, Suspense, lazy } from "react";
import type { ComponentProps, ComponentType, ReactNode } from "react";
import { Loading } from "./UI";

class LoadBoundary extends Component<
  { children: ReactNode },
  { failed: boolean }
> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    return this.state.failed ? (
      <div className="error-box" role="alert">
        <p>页面模块未能加载，重新加载页面后可重试。</p>
        <button className="btn" onClick={() => window.location.reload()}>
          重新加载页面
        </button>
      </div>
    ) : (
      this.props.children
    );
  }
}

// Each page/dialog owns its loading boundary. The shell and other retained
// pages keep their state while a module loads. Failed JS/CSS module requests
// can be cached by the browser, so recovery explicitly reloads the document.
export function lazyView<T extends ComponentType<any>>(
  load: () => Promise<{ default: T }>,
): ComponentType<ComponentProps<T>> {
  const View = lazy(load) as ComponentType<ComponentProps<T>>;
  return function LazyView(props: ComponentProps<T>) {
    return (
      <LoadBoundary>
        <Suspense fallback={<Loading />}>
          <View {...props} />
        </Suspense>
      </LoadBoundary>
    );
  };
}
