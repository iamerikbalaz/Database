import { useEffect, useState } from "react";
import { apiClient, type ApiClient } from "./api/client";
import { AppShell } from "./components/AppShell";
import { CompanyDetailPage } from "./pages/CompanyDetailPage";
import { CompaniesPage } from "./pages/CompaniesPage";
import { PlaceholderPage } from "./pages/PlaceholderPage";
import { ProjectDetailPage } from "./pages/ProjectDetailPage";
import { ProjectsPage } from "./pages/ProjectsPage";

interface AppProps { client?: ApiClient; initialPath?: string }
const normalizePath = (path: string) => path.split(/[?#]/)[0].replace(/\/+$/, "") || "/";

function App({ client = apiClient, initialPath }: AppProps) {
  const [path, setPath] = useState(() => normalizePath(initialPath ?? window.location.pathname));
  useEffect(() => {
    if (initialPath) return;
    const handlePopState = () => setPath(normalizePath(window.location.pathname));
    window.addEventListener("popstate", handlePopState);
    return () => window.removeEventListener("popstate", handlePopState);
  }, [initialPath]);
  const navigate = (destination: string) => {
    const nextPath = normalizePath(destination);
    if (!initialPath) window.history.pushState({}, "", nextPath);
    setPath(nextPath);
    if (!initialPath) window.scrollTo({ top: 0, behavior: "smooth" });
  };
  const companyMatch = path.match(/^\/companies\/([^/]+)$/);
  const projectMatch = path.match(/^\/projects\/([^/]+)$/);
  let page;
  if (path === "/" || path === "/dashboard") page = <PlaceholderPage title="Dashboard" description="Your workspace overview is coming next." />;
  else if (path === "/companies") page = <CompaniesPage client={client} navigate={navigate} />;
  else if (companyMatch) page = <CompanyDetailPage id={decodeURIComponent(companyMatch[1])} client={client} navigate={navigate} />;
  else if (path === "/projects") page = <ProjectsPage client={client} navigate={navigate} />;
  else if (projectMatch) page = <ProjectDetailPage id={decodeURIComponent(projectMatch[1])} client={client} navigate={navigate} />;
  else {
    const labels: Record<string, string> = { "/materials": "Materials", "/publication": "Publication", "/settings": "Settings" };
    page = labels[path] ? <PlaceholderPage title={labels[path]} description="This section is ready for the next implementation phase." /> : <PlaceholderPage title="Page not found" description="The requested page does not exist." />;
  }
  return <AppShell currentPath={path} navigate={navigate}>{page}</AppShell>;
}
export default App;
export type { AppProps };
