import { EditorPage } from "./pages/EditorPage";
import { MaterialsPage } from "./pages/MaterialsPage";
import { MaterialDetailPage } from "./pages/MaterialDetailPage";
import { MaterialEditorPage } from "./pages/MaterialEditorPage";
import { isMaterialId } from "./api/materialDto";
import { BrandDetailPage } from "./pages/BrandDetailPage";
import { useEffect, useState, useRef } from "react";
import { apiClient, type ApiClient } from "./api/client";
import { AppShell } from "./components/AppShell";
import { CompanyDetailPage } from "./pages/CompanyDetailPage";
import { CompaniesPage } from "./pages/CompaniesPage";
import { PlaceholderPage } from "./pages/PlaceholderPage";
import { ProjectDetailPage } from "./pages/ProjectDetailPage";
import { ProjectsPage } from "./pages/ProjectsPage";

interface AppProps {
  client?: ApiClient;
  initialPath?: string;
}
const normalizePath = (path: string) =>
  path.split(/[?#]/)[0].replace(/\/+$/, "") || "/";

function App({ client = apiClient, initialPath }: AppProps) {
  const [notice, setNotice] = useState("");
  const [path, setPath] = useState(() =>
    normalizePath(initialPath ?? window.location.pathname),
  );
  useEffect(() => {
    if (initialPath) return;
    const handlePopState = () => {
      setNotice("");
      setPath(normalizePath(window.location.pathname));
    };
    window.addEventListener("popstate", handlePopState);
    return () => window.removeEventListener("popstate", handlePopState);
  }, [initialPath]);
  const noticeRef = useRef<HTMLParagraphElement>(null);
  useEffect(() => {
    if (notice) noticeRef.current?.focus();
  }, [notice, path]);
  const navigate = (destination: string, message = "") => {
    setNotice(message);
    const nextPath = normalizePath(destination);
    if (!initialPath) window.history.pushState({}, "", nextPath);
    setPath(nextPath);
    if (!initialPath) window.scrollTo({ top: 0, behavior: "smooth" });
  };
  let invalidUrl = false;
  try {
    decodeURIComponent(path);
  } catch {
    invalidUrl = true;
  }
  const companyMatch = path.match(/^\/companies\/([^/]+)$/);
  const projectMatch = path.match(/^\/projects\/([^/]+)$/);
  const companyEdit = path.match(/^\/companies\/([^/]+)\/edit$/);
  const brandNew = path.match(/^\/companies\/([^/]+)\/brands\/new$/);
  const brandEdit = path.match(/^\/brands\/([^/]+)\/edit$/);
  const brandMatch = path.match(/^\/brands\/([^/]+)$/);
  const projectEdit = path.match(/^\/projects\/([^/]+)\/edit$/);
  const materialMatch = path.match(/^\/materials\/([^/]+)(\/edit)?$/);
  let page;
  if (invalidUrl)
    page = (
      <PlaceholderPage
        title="Page not found"
        description="The URL is malformed."
      />
    );
  else if (path === "/materials")
    page = <MaterialsPage client={client} navigate={navigate} />;
  else if (path === "/materials/new")
    page = <MaterialEditorPage key={path} client={client} navigate={navigate} onSaved={navigate} />;
  else if (materialMatch) {
    const id = decodeURIComponent(materialMatch[1]);
    page = !isMaterialId(id)
      ? <section><h1>Invalid material ID</h1><p role="alert">The material URL must contain a valid UUID.</p></section>
      : materialMatch[2]
        ? <MaterialEditorPage key={path} id={id} client={client} navigate={navigate} onSaved={navigate} />
        : <MaterialDetailPage key={path} id={id} client={client} navigate={navigate} />;
  }
  else if (path === "/companies/new")
    page = (
      <EditorPage
        key={path}
        kind="company"
        client={client}
        navigate={navigate}
        onSaved={navigate}
      />
    );
  else if (companyEdit)
    page = (
      <EditorPage
        key={path}
        kind="company"
        id={decodeURIComponent(companyEdit[1])}
        client={client}
        navigate={navigate}
        onSaved={navigate}
      />
    );
  else if (brandNew)
    page = (
      <EditorPage
        key={path}
        kind="brand"
        companyId={decodeURIComponent(brandNew[1])}
        client={client}
        navigate={navigate}
        onSaved={navigate}
      />
    );
  else if (brandEdit)
    page = (
      <EditorPage
        key={path}
        kind="brand"
        id={decodeURIComponent(brandEdit[1])}
        client={client}
        navigate={navigate}
        onSaved={navigate}
      />
    );
  else if (brandMatch)
    page = (
      <BrandDetailPage
        id={decodeURIComponent(brandMatch[1])}
        client={client}
        navigate={navigate}
      />
    );
  else if (path === "/projects/new")
    page = (
      <EditorPage
        key={path}
        kind="project"
        client={client}
        navigate={navigate}
        onSaved={navigate}
      />
    );
  else if (projectEdit)
    page = (
      <EditorPage
        key={path}
        kind="project"
        id={decodeURIComponent(projectEdit[1])}
        client={client}
        navigate={navigate}
        onSaved={navigate}
      />
    );
  else if (path === "/" || path === "/dashboard")
    page = (
      <PlaceholderPage
        title="Dashboard"
        description="Your workspace overview is coming next."
      />
    );
  else if (path === "/companies")
    page = <CompaniesPage client={client} navigate={navigate} />;
  else if (companyMatch)
    page = (
      <CompanyDetailPage
        id={decodeURIComponent(companyMatch[1])}
        client={client}
        navigate={navigate}
      />
    );
  else if (path === "/projects")
    page = <ProjectsPage client={client} navigate={navigate} />;
  else if (projectMatch)
    page = (
      <ProjectDetailPage
        id={decodeURIComponent(projectMatch[1])}
        client={client}
        navigate={navigate}
      />
    );
  else {
    const labels: Record<string, string> = {
      "/publication": "Publication",
      "/settings": "Settings",
    };
    page = labels[path] ? (
      <PlaceholderPage
        title={labels[path]}
        description="This section is ready for the next implementation phase."
      />
    ) : (
      <PlaceholderPage
        title="Page not found"
        description="The requested page does not exist."
      />
    );
  }
  return (
    <AppShell currentPath={path} navigate={navigate}>
      {notice && (
        <p
          ref={noticeRef}
          tabIndex={-1}
          role="status"
          className="success-notice"
        >
          {notice}
        </p>
      )}
      {page}
    </AppShell>
  );
}
export default App;
export type { AppProps };
