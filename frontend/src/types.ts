export type CompanyStatus = "active" | "inactive";
export type ProjectStatus = "not_started" | "in_progress" | "done";
export interface Company {
  id: string;
  name: string;
  officialName: string | null;
  country: string | null;
  address: string | null;
  websiteUrl: string | null;
  vatId: string | null;
  notionPageId: string | null;
  status: CompanyStatus;
  createdAt: string;
  updatedAt: string;
}
export interface PublishedBrand {
  id: string;
  companyId: string;
  name: string;
  folderPrefix: string;
  brandIdentifier: string;
  nextSequenceNumber: number;
  isActive: boolean;
  createdAt: string;
  updatedAt: string;
}
export interface Project {
  id: string;
  companyId: string;
  number: string;
  name: string;
  status: ProjectStatus;
  dueDate: string | null;
  description: string | null;
  createdAt: string;
  updatedAt: string;
}
export interface CompanyDetail extends Company {
  brands: PublishedBrand[];
  projects: Project[];
}
export interface ProjectDetail extends Project {
  clientName: string;
}
