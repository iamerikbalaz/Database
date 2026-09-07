export type CompanyStatus = "active" | "inactive";
export type ProjectStatus = "not_started" | "in_progress" | "review" | "done";
export interface Brand { id: string; companyId: string; name: string; identifier: string; active: boolean }
export interface Company { id: string; name: string; officialName?: string; status: CompanyStatus; websiteUrl?: string; email?: string; phone?: string; address?: string; description?: string }
export interface Project { id: string; number: string; name: string; companyId: string; clientName: string; status: ProjectStatus; dueDate?: string; description?: string }
export interface CompanyDetail extends Company { brands: Brand[]; projects: Project[] }
export interface ProjectDetail extends Project { createdAt: string; updatedAt: string }
