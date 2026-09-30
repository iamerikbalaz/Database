import { request } from "./client";
import { boolean, nullable, record, string, uuid } from "./dto";
import { ApiError } from "./errors";
import { apiUrl, sessionGeneration } from "../auth/sessionTransport";

export const customerStatuses = ["In library", "test", "Active"] as const;
export const orderStatuses = ["Not started", "Price offer sent", "Waiting for samples", "Samples Obtained", "Scanned", "Post-production", "Visualize", "Test complete", "Ongoing", "To be invoiced", "invoiced", "Done", "Canceled"] as const;
export const priorities = ["Low", "Medium", "High", "Urgent"] as const;
export type DirectoryValues = Record<string, string | boolean | null>;
export type DirectorySync = { state: string; error: string | null; pendingChanges: boolean; enabled: boolean | null } | null;
function sync(input: unknown): DirectorySync {
  if (!input) return null;
  const value = record(input);
  return { state: string(value.status), error: typeof value.error_code === "string" ? value.error_code : null, pendingChanges: value.pending_changes === true, enabled: typeof value.enabled === "boolean" ? value.enabled : null };
}
function common(input: unknown) {
  const value = record(input);
  return { id: uuid(value.id), notionPageId: nullable(value.notion_page_id), createdAt: string(value.created_at), updatedAt: string(value.updated_at), sync: sync(value.sync), folderStatus: sync(value.folder_operation ?? value.folder_status) };
}
export function parseCustomer(input: unknown) {
  const value = record(input);
  if (!Array.isArray(value.main_category_codes) || value.main_category_codes.some(code => typeof code !== "string")) throw new Error("Invalid customer categories");
  return { ...common(input), name: string(value.name), status: string(value.status), website: nullable(value.website), address: nullable(value.address),
    shippingAddress: nullable(value.shipping_address), legalName: nullable(value.legal_name), vatId: nullable(value.vat_id), description: nullable(value.description), notes: nullable(value.notes),
    brandIdentifier: nullable(value.brand_identifier), folderPrefix: string(value.folder_prefix), isActive: boolean(value.is_active),
    mainCategoryCodes: value.main_category_codes as string[], hasLogo: boolean(value.has_logo), legacyCompanyId: value.legacy_company_id == null ? null : uuid(value.legacy_company_id) };
}
export function parseOrder(input: unknown) {
  const value = record(input);
  return { ...common(input), number: string(value.number), customerId: value.customer_id == null ? null : uuid(value.customer_id), projectType: nullable(value.project_type),
    startingDate: nullable(value.starting_date), dueDate: nullable(value.due_date), notes: nullable(value.notes), responsibleId: value.responsible_id == null ? null : uuid(value.responsible_id),
    responsibleNotionPageIds: Array.isArray(value.responsible_notion_page_ids) ? value.responsible_notion_page_ids.map(uuid) : [],
    status: string(value.status), priority: nullable(value.priority), generatedName: string(value.generated_name), folderPath: nullable(value.folder_path),
    folderNameMatches: value.folder_name_matches === undefined ? true : boolean(value.folder_name_matches) };
}
export type Customer = ReturnType<typeof parseCustomer>;
export type Order = ReturnType<typeof parseOrder>;
export type CustomerRenameRequest = { name: string; folder_prefix?: string; rename_materials: boolean; expected_updated_at: string };
export type CustomerRenamePlan = { proposalHash: string; ready: boolean; materialCount: number; issues: string[] };
export type CustomerRenameOperation = { id: string; status: string; completedCount: number; totalCount: number; customer: Customer | null; failures: string[] };
function renameOperation(input: unknown): CustomerRenameOperation {
  const value = record(input);
  return { id: uuid(value.id), status: string(value.status), completedCount: Number(value.completed_count), totalCount: Number(value.total_count), customer: value.customer ? parseCustomer(value.customer) : null,
    failures: Array.isArray(value.materials) ? value.materials.map(record).filter(item => item.failure_code).map(item => `${string(item.material_id)}: ${string(item.failure_code)}`) : [] };
}
function list<T>(input: unknown, parse: (value: unknown) => T): T[] { if (!Array.isArray(input)) throw new Error("Invalid directory list"); return input.map(parse); }
export const directoryClient = {
  async customers() { return list(await request("/customers"), parseCustomer); },
  async customer(id: string) { return parseCustomer(await request(`/customers/${uuid(id)}`)); },
  async saveCustomer(id: string | undefined, payload: DirectoryValues, key: string) { return parseCustomer(await request(id ? `/customers/${uuid(id)}` : "/customers", id ? "PATCH" : "POST", payload, key)); },
  async customerRenamePlan(id: string, payload: CustomerRenameRequest): Promise<CustomerRenamePlan> {
    const value = record(await request(`/customers/${uuid(id)}/rename-plan`, "POST", payload));
    return { proposalHash: string(value.proposal_hash), ready: boolean(value.ready), materialCount: Number(value.material_count), issues: Array.isArray(value.issues) ? value.issues.map(record).map(issue => typeof issue.message === "string" ? issue.message : string(issue.code)) : [] };
  },
  async renameCustomer(id: string, payload: CustomerRenameRequest, proposalHash: string, key: string) { return renameOperation(await request(`/customers/${uuid(id)}/rename`, "POST", { ...payload, confirmed: true, expected_proposal_hash: proposalHash }, key)); },
  async customerRenameOperations(id: string) { const result = await request(`/customers/${uuid(id)}/rename-operations`); return list(Array.isArray(result) ? result : record(result).items, renameOperation); },
  async resumeCustomerRename(id: string, operationId: string) { return renameOperation(await request(`/customers/${uuid(id)}/rename-operations/${uuid(operationId)}/resume`, "POST")); },
  async orders() { return list(await request("/orders"), parseOrder); },
  async order(id: string) { return parseOrder(await request(`/orders/${uuid(id)}`)); },
  async saveOrder(id: string | undefined, payload: DirectoryValues, key: string) { return parseOrder(await request(id ? `/orders/${uuid(id)}` : "/orders", id ? "PATCH" : "POST", payload, key)); },
  async logo(customer: Customer, file: File, key: string) {
    const generation = sessionGeneration();
    if (!file.size || file.size > 2 * 1024 * 1024 || !["image/png", "image/jpeg", "image/webp"].includes(file.type)) throw new Error("Choose a PNG, JPEG or WebP image up to 2 MB.");
    const content = await new Promise<string>((resolve, reject) => { const reader = new FileReader(); reader.onerror = () => reject(new Error("The image could not be read.")); reader.onload = () => resolve(String(reader.result).split(",")[1]); reader.readAsDataURL(file); });
    if (generation !== sessionGeneration()) throw new ApiError(409, "The session changed while reading the image. Sign in again before uploading.");
    return parseCustomer(await request(`/customers/${uuid(customer.id)}/logo`, "PUT", { content_base64: content, filename: file.name, expected_updated_at: customer.updatedAt }, key));
  },
  logoUrl(customer: Customer) { return apiUrl(`/customers/${uuid(customer.id)}/logo`) + `?v=${encodeURIComponent(customer.updatedAt)}`; },
  async sync(kind: "CUSTOMER" | "ORDER", id: string) { return sync(await request(`/notion-sync/${kind}/${uuid(id)}`)); },
  async retrySync(kind: "CUSTOMER" | "ORDER", id: string) { return request(`/notion-sync/${kind}/${uuid(id)}/retry`, "POST"); },
  async history(kind: "customer" | "order", id: string) {
    const result = record(await request(`/${kind}s/${uuid(id)}/history`));
    return list(result.items, input => { const row = record(input); return { id: uuid(row.id), actorId: uuid(row.actor_id), actorName: nullable(row.actor_name), action: string(row.action), source: typeof row.source === "string" ? row.source : null, before: row.before === null ? null : record(row.before), after: record(row.after), createdAt: string(row.created_at) }; });
  },
  async folderInfo(id: string) { const value = record(await request(`/orders/${uuid(id)}/folder`)); return { enabled: value.enabled === true }; },
  async folder(order: Order, action: "CREATE" | "RENAME", key: string) { return request(`/orders/${uuid(order.id)}/folder`, "POST", { action, expected_updated_at: order.updatedAt, confirmed: true }, key); },
};
