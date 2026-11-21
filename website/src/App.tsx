import { lazy, Suspense, useEffect } from "react";
import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { Layout } from "./components/Layout";
import { HomePage } from "./pages/HomePage";

const DocsPage = lazy(() => import("./pages/DocsPage").then((m) => ({ default: m.DocsPage })));
const ArchitecturePage = lazy(() =>
  import("./pages/ArchitecturePage").then((m) => ({ default: m.ArchitecturePage }))
);
const ReferencePage = lazy(() =>
  import("./pages/ReferencePage").then((m) => ({ default: m.ReferencePage }))
);
const ReleasesPage = lazy(() =>
  import("./pages/ReleasesPage").then((m) => ({ default: m.ReleasesPage }))
);
const FieldKitPage = lazy(() =>
  import("./pages/FieldKitPage").then((m) => ({ default: m.FieldKitPage }))
);

function ScrollManager() {
  const { pathname, hash } = useLocation();
  useEffect(() => {
    if (!hash) {
      window.scrollTo({ top: 0 });
    }
  }, [pathname, hash]);
  return null;
}

function RouteFallback() {
  return (
    <div className="container" style={{ padding: "64px 28px", color: "var(--ink-muted)" }}>
      <span className="mono" style={{ fontSize: 12 }}>
        Loading section…
      </span>
    </div>
  );
}

export function App() {
  const basename = import.meta.env.BASE_URL.replace(/\/+$/, "") || "/";
  return (
    <BrowserRouter basename={basename === "/" ? undefined : basename}>
      <ScrollManager />
      <Layout>
        <Suspense fallback={<RouteFallback />}>
          <Routes>
            <Route path="/" element={<HomePage />} />
            <Route path="/docs" element={<Navigate to="/docs/getting-started" replace />} />
            <Route path="/docs/:slug" element={<DocsPage />} />
            <Route path="/architecture" element={<ArchitecturePage />} />
            <Route path="/reference" element={<ReferencePage />} />
            <Route path="/releases" element={<ReleasesPage />} />
            <Route path="/field-kit" element={<FieldKitPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </Suspense>
      </Layout>
    </BrowserRouter>
  );
}
