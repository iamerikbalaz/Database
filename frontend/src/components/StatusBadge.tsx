import type { CompanyStatus, ProjectStatus } from "../types";
const labels: Record<CompanyStatus | ProjectStatus,string>={active:"Active",inactive:"Inactive",not_started:"Not started",in_progress:"In progress",done:"Done"};
export function StatusBadge({status}:{status:CompanyStatus|ProjectStatus}){return <span className={`badge badge--${status}`}><span aria-hidden="true"/>{labels[status]}</span>}
