// Compatibility coverage for the legacy API editors retained during migration.
// The production router exposes DirectoryRecordPage instead.
import { useEffect, useRef, useState } from "react";
import { httpApiClient } from "../api/client";
import { EditorPage } from "../pages/EditorPage";
import { CompaniesPage } from "../pages/CompaniesPage";
import { CompanyDetailPage } from "../pages/CompanyDetailPage";
import { ProjectDetailPage } from "../pages/ProjectDetailPage";
import { BrandDetailPage } from "../pages/BrandDetailPage";
import { ProjectsPage } from "../pages/ProjectsPage";
export default function LegacyDirectoryForms({ initialPath }: { initialPath: string }) {
  const [path, setPath] = useState(initialPath), [message, setMessage] = useState("");
  const notice = useRef<HTMLParagraphElement>(null);
  useEffect(() => { if (message) notice.current?.focus(); }, [message]);
  const navigate = (next: string, text = "") => { setPath(next); setMessage(text); };
  const match = path.match(/^\/(companies|brands|projects)\/([^/]+)(\/edit|\/brands\/new)?$/);
  let page;
  if (match && (match[2] === "new" || match[3])) {
    const brandNew = match[3] === "/brands/new";
    page = <EditorPage key={path} kind={brandNew || match[1] === "brands" ? "brand" : match[1] === "projects" ? "project" : "company"} id={brandNew || match[2] === "new" ? undefined : match[2]} companyId={brandNew ? match[2] : undefined} client={httpApiClient} navigate={navigate} onSaved={navigate} />;
  } else if (match) page = match[1] === "companies" ? <CompanyDetailPage id={match[2]} client={httpApiClient} navigate={navigate} /> : match[1] === "projects" ? <ProjectDetailPage id={match[2]} client={httpApiClient} navigate={navigate} /> : <BrandDetailPage id={match[2]} client={httpApiClient} navigate={navigate} />;
  else page = path === "/projects" ? <ProjectsPage client={httpApiClient} navigate={navigate} /> : <CompaniesPage client={httpApiClient} navigate={navigate} />;
  return <><a href="/projects" onClick={event => { event.preventDefault(); navigate("/projects"); }}>Projects</a>{message && <p role="status" tabIndex={-1} ref={notice}>{message}</p>}{page}</>;
}
