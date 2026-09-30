import { vi } from "vitest";
import { directoryClient, parseCustomer, parseOrder } from "../api/directoryClient";
import { mockApiClient } from "../api/client";
import { companies, brands, projects } from "../api/mockData";
export const customerDtos = companies.map((company, index) => ({ id: brands[index]?.id ?? company.id, legacy_company_id: company.id, name: company.name, status: "Active", website: company.websiteUrl, address: company.address, shipping_address: null, legal_name: company.officialName, vat_id: company.vatId, description: null, notes: null, brand_identifier: company.name.toLowerCase(), folder_prefix: company.name.toUpperCase(), is_active: true, main_category_codes: [index ? "B01" : "H01"], has_logo: false, logo_url: null, notion_page_id: null, sync: { status: "PENDING", enabled: false, error_code: null, pending_changes: true }, created_at: company.createdAt, updated_at: company.updatedAt }));
export const orderDtos = projects.map((project, index) => ({ id: project.id, number: String(index + 1).padStart(4, "0"), customer_id: customerDtos.find(customer => customer.legacy_company_id === project.companyId)?.id ?? customerDtos[0].id, project_type: "SCANNING_FABRICS", starting_date: "2026-09-01", due_date: project.dueDate, notes: project.description, responsible_id: null, responsible_notion_page_ids: [], status: "Done", priority: "Medium", generated_name: `000${index + 1}_SWISSPEARL_SCANNING_FABRICS_092026`, folder_path: null, folder_name_matches: true, notion_page_id: null, sync: { status: "PENDING", enabled: false, error_code: null, pending_changes: true }, folder_status: null, created_at: project.createdAt, updated_at: project.updatedAt }));
export const customers = customerDtos.map(parseCustomer), orders = orderDtos.map(parseOrder);
export function mockDirectory() {
  vi.spyOn(mockApiClient, "getInternalUsers").mockResolvedValue([]);
  vi.spyOn(directoryClient, "customers").mockResolvedValue(customers);
  vi.spyOn(directoryClient, "orders").mockResolvedValue(orders);
  vi.spyOn(directoryClient, "customer").mockImplementation(async id => { const item = customers.find(row => row.id === id); if (!item) throw new Error("missing"); return item; });
  vi.spyOn(directoryClient, "order").mockImplementation(async id => { const item = orders.find(row => row.id === id); if (!item) throw new Error("missing"); return item; });
  vi.spyOn(directoryClient, "folderInfo").mockResolvedValue({ enabled: false });
}
