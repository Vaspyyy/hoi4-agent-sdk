import { useEffect, useState, type ReactNode } from "react";
import { Link, NavLink, useLocation } from "react-router-dom";
import { Menu, Moon, Search, Sun, X, ArrowUpRight } from "lucide-react";
import metaJson from "../generated/meta.json";
import type { SdkMeta } from "../types/sdk";
import { CommandPalette } from "./CommandPalette";

const META = metaJson as SdkMeta;

const NAV_ITEMS = [
  { to: "/docs/getting-started", label: "Docs", matchPrefix: "/docs" },
  { to: "/architecture", label: "Architecture", matchPrefix: "/architecture" },
  { to: "/reference", label: "Reference", matchPrefix: "/reference" },
  { to: "/releases", label: "Releases", matchPrefix: "/releases" },
  { to: "/field-kit", label: "Field Kit", matchPrefix: "/field-kit" },
];

function BrandMark() {
  return (
    <span className="brand-mark" aria-hidden="true">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
        <circle cx="12" cy="12" r="6.5" stroke="currentColor" strokeWidth="1.5" />
        <circle cx="12" cy="12" r="2" fill="var(--accent)" />
        <path
          d="M12 2.5V6.5M12 17.5V21.5M2.5 12H6.5M17.5 12H21.5"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinecap="square"
        />
      </svg>
    </span>
  );
}

export function Layout({ children }: { children: ReactNode }) {
  const [theme, setTheme] = useState<"light" | "dark">(() => {
    return (document.documentElement.getAttribute("data-theme") as "light" | "dark") || "light";
  });
  const [cmdOpen, setCmdOpen] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const location = useLocation();

  useEffect(() => {
    setMobileOpen(false);
  }, [location.pathname]);

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setCmdOpen((prev) => !prev);
      } else if (
        e.key === "/" &&
        !cmdOpen &&
        !(document.activeElement instanceof HTMLInputElement) &&
        !(document.activeElement instanceof HTMLTextAreaElement)
      ) {
        e.preventDefault();
        setCmdOpen(true);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [cmdOpen]);

  const toggleTheme = () => {
    const next = theme === "light" ? "dark" : "light";
    setTheme(next);
    document.documentElement.setAttribute("data-theme", next);
    try {
      localStorage.setItem("hoi4-sdk-theme", next);
    } catch {
      // ignore storage failure
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", minHeight: "100vh" }}>
      <header className="site-header">
        <div className="container header-inner">
          <div style={{ display: "flex", alignItems: "center", gap: 22 }}>
            <Link to="/" className="brand">
              <BrandMark />
              <span className="brand-name">hoi4-agent-sdk</span>
              <span className="brand-version">v{META.version}</span>
            </Link>

            <nav className="nav-links" aria-label="Main navigation">
              {NAV_ITEMS.map((item) => {
                const isActive = location.pathname.startsWith(item.matchPrefix);
                return (
                  <NavLink
                    key={item.to}
                    to={item.to}
                    className={`nav-link ${isActive ? "active" : ""}`}
                  >
                    {item.label}
                  </NavLink>
                );
              })}
            </nav>
          </div>

          <div className="header-actions">
            <button
              type="button"
              className="search-trigger"
              onClick={() => setCmdOpen(true)}
              aria-label="Open command search"
            >
              <Search size={14} />
              <span>Search SDK…</span>
              <kbd>⌘K</kbd>
            </button>

            <a
              href={META.repository}
              target="_blank"
              rel="noreferrer"
              className="btn btn-ghost btn-sm"
              style={{ gap: 5 }}
            >
              GitHub
              <ArrowUpRight size={13} />
            </a>

            <button
              type="button"
              className="icon-btn"
              onClick={toggleTheme}
              aria-label={theme === "light" ? "Switch to dark theme" : "Switch to light theme"}
              title={theme === "light" ? "Night Operations (Dark)" : "Archival Paper (Light)"}
            >
              {theme === "light" ? <Moon size={15} /> : <Sun size={15} />}
            </button>

            <button
              type="button"
              className="icon-btn mobile-menu-btn"
              onClick={() => setMobileOpen((o) => !o)}
              aria-label="Toggle menu"
            >
              {mobileOpen ? <X size={16} /> : <Menu size={16} />}
            </button>
          </div>
        </div>
      </header>

      {mobileOpen && (
        <div className="mobile-drawer">
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              className={`nav-link ${location.pathname.startsWith(item.matchPrefix) ? "active" : ""}`}
            >
              {item.label}
            </NavLink>
          ))}
        </div>
      )}

      <main style={{ flex: 1 }}>{children}</main>

      <footer className="site-footer">
        <div className="container">
          <div className="footer-grid">
            <div>
              <div className="brand" style={{ marginBottom: 12 }}>
                <BrandMark />
                <span className="brand-name">hoi4-agent-sdk</span>
              </div>
              <p style={{ margin: "0 0 14px", color: "var(--ink-secondary)", maxWidth: "42ch", fontSize: 13.5 }}>
                A source-preserving Python toolkit for creating, inspecting, validating, and editing Hearts of Iron IV mods.
                Built with zero mandatory runtime dependencies for automation, desktop tools, and coding agents.
              </p>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                <span className="tag">Python {META.requiresPython}</span>
                <span className="tag">{META.license} License</span>
                <span className="tag tag-green">{META.stats.runtimeDependencies} Core Deps</span>
              </div>
            </div>

            <div className="footer-col">
              <h4>Documentation</h4>
              <ul>
                <li><Link to="/docs/getting-started">Getting Started</Link></li>
                <li><Link to="/docs/api">API Reference</Link></li>
                <li><Link to="/docs/models">Data Models</Link></li>
                <li><Link to="/docs/recipes">Common Recipes</Link></li>
                <li><Link to="/docs/agent-traps">Agent Traps</Link></li>
              </ul>
            </div>

            <div className="footer-col">
              <h4>Internals &amp; Tools</h4>
              <ul>
                <li><Link to="/architecture">30-Layer Mod Graph</Link></li>
                <li><Link to="/docs/internals-parser">Lossless Syntax Tree</Link></li>
                <li><Link to="/reference">Interactive Catalogs</Link></li>
                <li><Link to="/field-kit">Field Kit &amp; Config</Link></li>
                <li><Link to="/docs/contributing">Contributing</Link></li>
              </ul>
            </div>

            <div className="footer-col">
              <h4>Project</h4>
              <ul>
                <li><Link to="/releases">Release Notes ({META.stats.releases})</Link></li>
                <li><Link to="/releases#unreleased">Unreleased (Lossless Tree)</Link></li>
                <li>
                  <a href={META.repository} target="_blank" rel="noreferrer">
                    GitHub Repository
                  </a>
                </li>
                <li>
                  <a href={META.issues} target="_blank" rel="noreferrer">
                    Issue Tracker
                  </a>
                </li>
                <li>
                  <a href="https://pypi.org/project/hoi4-agent-sdk/" target="_blank" rel="noreferrer">
                    PyPI Package
                  </a>
                </li>
              </ul>
            </div>
          </div>

          <div className="footer-bottom">
            <span>
              HOI4 Agent SDK v{META.version} · {META.stats.modMethods} facade methods · {META.stats.validationCodes} validation codes · {META.stats.definitionKinds} common/ domains
            </span>
            <span>All docs, catalogs, and release notes extracted directly from source.</span>
          </div>
        </div>
      </footer>

      <CommandPalette open={cmdOpen} onClose={() => setCmdOpen(false)} />
    </div>
  );
}
