import { useCallback, useEffect, useRef, useState } from "react";
import type { ApiClient } from "../api/client";
import { ApiError } from "../api/errors";
import { directoryClient, customerStatuses, orderStatuses, priorities, type Customer, type Order, type DirectoryValues } from "../api/directoryClient";
import { useResource } from "../api/useResource";
import { useSession } from "../auth/context";
import { sessionGeneration } from "../auth/sessionTransport";
import { NavigationLink } from "../components/NavigationLink";
import { DirectoryHistory } from "../components/DirectoryHistory";
import { CustomerRenamePanel } from "../components/CustomerRenamePanel";
import { DirectorySync } from "../components/DirectorySync";
import { ErrorState, LoadingState } from "../components/PageState";
import { NasFolderReference } from "../components/NasFolderReference";
import { catalogMaterialCategoryLabels, categoryLabel } from "../data/materialCategories";
import { catalogClient } from "../api/catalogClient";
import { useNavigationGuard } from "../navigationGuard";
import type { InternalUser } from "../api/materialDto";
import { OrderMaterialsPanel } from "../components/OrderMaterialsPanel";
import "./DirectoryRecordPage.css";

type Kind = "customer" | "order";
type Props = { kind: Kind; id?: string; client: ApiClient; navigate: (path: string) => void; onSaved: (path: string, message?: string) => void };
export function DirectoryRecordPage(props: Props) {
  const { kind, id, client } = props;
  const resource = useResource(useCallback(async () => {
    const [item, customers, users, orders, folder, defaults] = await Promise.all([
      id ? kind === "customer" ? directoryClient.customer(id) : directoryClient.order(id) : Promise.resolve(undefined),
      kind === "order" ? directoryClient.customers() : Promise.resolve([]),
      kind === "order" ? client.getInternalUsers() : Promise.resolve([]),
      kind === "customer" && id ? directoryClient.orders() : Promise.resolve([]),
      kind === "order" && id ? directoryClient.folderInfo(id) : Promise.resolve({ enabled: false }),
      kind === "order" && !id ? directoryClient.orderDefaults() : Promise.resolve(undefined),
    ]);
    return { item, customers, users, orders, folderEnabled: folder.enabled, defaults };
  }, [kind, id, client]));
  if (resource.error) return <ErrorState message={`This ${kind} could not be loaded.`} retry={resource.retry} />;
  if (!resource.data) return <LoadingState label={`Loading ${kind}…`} />;
  return <DirectoryRecordEditor key={`${kind}:${id}:${resource.data.item?.updatedAt ?? "new"}`} {...props} {...resource.data} refresh={resource.retry} />;
}

type Field = { key: string; label: string; required?: boolean; type?: "date" | "textarea" | "url" | "checkbox"; options?: { value: string; label: string; disabled?: boolean }[]; max?: number };
function DirectoryRecordEditor({ kind, id, item, customers, users, orders, folderEnabled, defaults, client, navigate, onSaved, refresh }: Props & { item?: Customer | Order; customers: Customer[]; users: InternalUser[]; orders: Order[]; folderEnabled: boolean; defaults?: { number: string; startingDate: string }; refresh: () => void }) {
  const catalog = useResource(useCallback(() => kind === "customer" ? catalogClient.categories() : Promise.resolve([]), [kind]));
  const customer = kind === "customer" ? item as Customer | undefined : undefined, order = kind === "order" ? item as Order | undefined : undefined;
  const role = useSession()?.session.user.role, canEdit = role === "ADMIN" || role === "PRODUCTION_LEAD";
  const initial: DirectoryValues = kind === "customer" ? {
    name: customer?.name ?? "", brand_identifier: customer?.brandIdentifier ?? "", status: customer?.status ?? "Active cooperation", is_published: customer?.isPublished ?? false, country: customer?.country ?? "", website: customer?.website ?? "", address: customer?.address ?? "", shipping_address: customer?.shippingAddress ?? "", legal_name: customer?.legalName ?? "", vat_id: customer?.vatId ?? "", description: customer?.description ?? "", notes: customer?.notes ?? "",
  } : { number: order?.number ?? defaults?.number ?? "", customer_id: order?.customerId ?? "", project_type: order?.projectType ?? "", starting_date: order?.startingDate ?? defaults?.startingDate ?? "", due_date: order?.dueDate ?? "", notes: order?.notes ?? "", responsible_id: order?.responsibleId ?? "", status: order?.status ?? "Not started", priority: order?.priority ?? "" };
  const [values, setValues] = useState(initial), [busy, setBusy] = useState(false), [uncertain, setUncertain] = useState(false), [error, setError] = useState(""), [notice, setNotice] = useState(""), [requestGeneration, setRequestGeneration] = useState<number>(), [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const summary = useRef<HTMLParagraphElement>(null);
  useEffect(() => { if (error) summary.current?.focus(); }, [error]);
  const [file, setFile] = useState<File>(), [folderAction, setFolderAction] = useState<"CREATE" | "RENAME" | null>(null);
  const [renameBusy, setRenameBusy] = useState(false);
  const packet = useRef<{ key: string; generation: number; run: (key: string) => Promise<unknown>; done: (result: unknown) => void } | null>(null), sending = useRef(false), alive = useRef(true), dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => { if (folderAction) dialog.current?.showModal(); }, [folderAction]);
  useNavigationGuard(() => packet.current !== null);
  useEffect(() => { if (!busy && !uncertain) return; const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; }; window.addEventListener("beforeunload", warn); return () => window.removeEventListener("beforeunload", warn); }, [busy, uncertain]);
  const submit = async (operation?: { run: (key: string) => Promise<unknown>; done: (result: unknown) => void }) => {
    if (sending.current) return;
    if (!packet.current && operation) { const generation = sessionGeneration(); packet.current = { ...operation, key: crypto.randomUUID(), generation }; setRequestGeneration(generation); }
    const current = packet.current;
    if (!current || current.generation !== sessionGeneration()) { setError("The session changed. Sign in again before continuing."); return; }
    const wasUncertain = uncertain;
    sending.current = true; setBusy(true); setError(""); setNotice(""); setFieldErrors({});
    try {
      const result = await current.run(current.key);
      packet.current = null;
      if (!alive.current || current.generation !== sessionGeneration()) return;
      setUncertain(false); current.done(result);
    } catch (cause) {
      if (!alive.current) return;
      if (!wasUncertain && cause instanceof ApiError && cause.status >= 400 && cause.status < 500) {
        packet.current = null; setUncertain(false); setFieldErrors(Object.fromEntries(cause.issues.map(issue => [issue.field, issue.message]))); setError((cause.code === "BRAND_SOURCE_REWRITE_REQUIRED" ? "The customer name also appears in material metadata. Existing linked materials need a coordinated update before this name can change." : cause.message) + (cause.issues.length ? " " + cause.issues.map(issue => `${issue.field}: ${issue.message}`).join("; ") : ""));
      } else { setUncertain(true); setError("The result could not be confirmed. Retry the same request to recover it without creating a duplicate."); }
    } finally { sending.current = false; if (alive.current) setBusy(false); }
  };
  const fields: Field[] = kind === "customer" ? [
    ...(!id ? [{ key: "name", label: "Name", required: true }] : []), { key: "brand_identifier", label: "Brand identifier" },
    { key: "status", label: "Status", options: customerStatuses.map(value => ({ value, label: value })) },
    { key: "website", label: "Website", type: "url" }, { key: "legal_name", label: "Legal name" }, { key: "vat_id", label: "VAT ID" },
    { key: "country", label: "Country" }, { key: "is_published", label: "Published", type: "checkbox" },
    { key: "address", label: "Address", type: "textarea" }, { key: "shipping_address", label: "Shipping address", type: "textarea" },
    { key: "description", label: "Company description", type: "textarea" }, { key: "notes", label: "Notes", type: "textarea" },
  ] : [
    ...(!id ? [{ key: "number", label: "Number (automatic if empty)", max: 4 }] : []),
    { key: "customer_id", label: "Customer", required: !id, options: [{ value: "", label: "Choose a customer", disabled: true }, ...customers.map(value => ({ value: value.id, label: value.name }))] },
    { key: "project_type", label: "Project type", required: !id }, { key: "starting_date", label: "Starting date", type: "date", required: !id },
    { key: "due_date", label: "Due date", type: "date" }, { key: "status", label: "Status", options: orderStatuses.map(value => ({ value, label: value })) },
    { key: "priority", label: "Priority", options: [{ value: "", label: "Not set" }, ...priorities.map(value => ({ value, label: value }))] },
    { key: "responsible_id", label: "Responsible", options: [{ value: "", label: "Not assigned" }, ...users.filter(value => value.role === "PROCESSOR" && value.isActive || value.id === order?.responsibleId).map(value => ({ value: value.id, label: value.displayName + (value.isActive ? "" : " (inactive)"), disabled: !value.isActive || value.role !== "PROCESSOR" }))] },
    { key: "notes", label: "Note", type: "textarea" },
  ];
  const locked = busy || uncertain || renameBusy || !canEdit;
  const title = id ? kind === "customer" ? customer!.name : `Order ${order!.number}` : `Add ${kind}`;
  const date = String(values.starting_date ?? "");
  const generatedPreview = [order?.number ?? String(values.number || "0000"), customers.find(value => value.id === values.customer_id)?.name ?? "", values.project_type ?? "", /^\d{4}-\d{2}-\d{2}$/.test(date) ? date.slice(5, 7) + date.slice(0, 4) : ""].join("_").toUpperCase();
  return <section className="directory-record directory-record--compact"><NavigationLink className="back-link" href={`/${kind}s`} navigate={navigate}>Back to {kind}s</NavigationLink>
    <div className="page-heading"><div><p className="eyebrow">{kind === "customer" ? "Directory" : "Production"}</p><div className="directory-record-name"><h1>{title}</h1>
      {customer && canEdit && <CustomerRenamePanel customer={customer} disabled={busy || uncertain} onChanged={refresh} onBusyChange={setRenameBusy} />}
    </div>{order && <p>{order.generatedName}</p>}</div>{item && <DirectorySync detailed sync={item.sync} notionPageId={item.notionPageId} />}</div>
    {error && <p ref={summary} tabIndex={-1} role="alert" className="form-error">{error}</p>}{notice && <p role="status">{notice}</p>}
    {uncertain && <button className="button" disabled={busy || requestGeneration !== sessionGeneration()} onClick={() => void submit()}>Retry same request</button>}
    <form noValidate className="panel record-form" aria-label={`${id ? "Edit" : "Add"} ${kind}`} onSubmit={event => {
      event.preventDefault();
      const errors: Record<string, string> = {};
      for (const field of fields) {
        const text = String(values[field.key] ?? "").trim();
        if (field.required && !text) errors[field.key] = `${field.label} is required.`;
        if (text.length > (field.max ?? (field.type === "textarea" ? 10000 : 255))) errors[field.key] = `${field.label} is too long.`;
        if (field.type === "url" && text) { try { const url = new URL(text); if (!["http:", "https:"].includes(url.protocol)) errors[field.key] = "Website must use http or https."; } catch { errors[field.key] = "Website must be a valid URL."; } }
        if (field.key === "number" && text && !/^\d{4}$/.test(text)) errors[field.key] = "Number must contain four digits.";
      }
      if (Object.keys(errors).length) { setFieldErrors(errors); setError(Object.values(errors).join(" ")); return; }
      const payload: DirectoryValues = {};
      for (const field of fields) if (!id || values[field.key] !== initial[field.key]) payload[field.key] = typeof values[field.key] === "string" ? String(values[field.key]).trim() || null : values[field.key];
      if (!id && kind === "order" && (!payload.number || payload.number === defaults?.number)) delete payload.number;
      if (id) payload.expected_updated_at = item!.updatedAt;
      if (!Object.keys(payload).some(key => key !== "expected_updated_at")) { setNotice("No changes to save."); return; }
      void submit({ run: key => kind === "customer" ? directoryClient.saveCustomer(id, payload, key) : directoryClient.saveOrder(id, payload, key), done: result => {
        const saved = result as Customer | Order;
        if (id) refresh(); else onSaved(`/${kind}s/${saved.id}`, `${kind === "customer" ? "Customer" : "Order"} created. Notion synchronization is queued.`);
      } });
    }}><fieldset disabled={locked}><legend>{kind === "customer" ? "Customer properties" : "Order properties"}</legend><div className="directory-fields">
      {fields.map(field => <label key={field.key} className={`directory-field${field.type === "checkbox" ? " directory-field--checkbox" : ""}`}><span>{field.label}{field.required ? " *" : ""}</span>
        {field.type === "checkbox" ? <input type="checkbox" checked={values[field.key] === true} onChange={event => setValues(previous => ({ ...previous, [field.key]: event.target.checked }))} /> : field.options ? <select aria-invalid={Boolean(fieldErrors[field.key])} required={field.required} value={String(values[field.key] ?? "")} onChange={event => setValues(previous => ({ ...previous, [field.key]: event.target.value }))}>{field.options.map(option => <option key={option.value} value={option.value} disabled={option.disabled}>{option.label}</option>)}</select>
          : field.type === "textarea" ? <textarea aria-invalid={Boolean(fieldErrors[field.key])} rows={2} maxLength={10000} value={String(values[field.key] ?? "")} onChange={event => setValues(previous => ({ ...previous, [field.key]: event.target.value }))} />
            : <input autoFocus={!id && field.key === fields[0]?.key} aria-invalid={Boolean(fieldErrors[field.key])} type={field.type ?? "text"} required={field.required} maxLength={field.max ?? 2048} pattern={field.key === "number" ? "[0-9]{4}" : undefined} value={String(values[field.key] ?? "")} onChange={event => setValues(previous => ({ ...previous, [field.key]: event.target.value }))} />}
      </label>)}
    </div>{order && order.responsibleNotionPageIds.length > 1 && <p>This order also has {order.responsibleNotionPageIds.length - 1} linked people in Notion. Choosing a different Responsible replaces that list with the selected person.</p>}{kind === "order" && <p className="directory-generated-name"><strong>Generated name:</strong> {generatedPreview}{!id && <small>The number is assigned when the order is saved. Its folder is then created in the configured project location.</small>}</p>}
    {canEdit && <button className="button button--primary" type="submit">{busy ? "Saving…" : id ? `Save ${kind}` : `Create ${kind}`}</button>}
    </fieldset></form>
    {customer && <div className="directory-related-panels"><article className="panel directory-logo-panel"><h2>Logo</h2>{customer.hasLogo && <img className="customer-logo" src={directoryClient.logoUrl(customer)} alt={`${customer.name} logo`} />}
      {canEdit && <form onSubmit={event => { event.preventDefault(); if (file) { const selectedFile = file; void submit({ run: key => directoryClient.logo(customer, selectedFile, key), done: () => refresh() }); } }}><fieldset disabled={locked}><label>Upload logo<input type="file" accept="image/png,image/jpeg,image/webp" onChange={event => { const selected = event.target.files?.[0]; if (selected && (!selected.size || selected.size > 2 * 1024 * 1024 || !["image/png", "image/jpeg", "image/webp"].includes(selected.type))) { setFile(undefined); setError("Choose a PNG, JPEG or WebP image up to 2 MB."); } else { setError(""); setFile(selected); } }} /></label><p>PNG, JPEG or WebP, up to 2 MB.</p><button className="button" disabled={!file}>Save logo</button></fieldset></form>}
    </article><article className="panel"><h2>Main categories</h2><p>{customer.mainCategoryCodes.map(code => categoryLabel(code, catalogMaterialCategoryLabels(catalog.data ?? []))).join(", ") || "No materials assigned yet."}</p><p className="muted">Categories follow the main category of this customer's materials.</p></article>
      <article className="panel"><h2>Orders</h2><div className="directory-related-orders">{orders.filter(value => value.customerId === id).map(value => <NavigationLink key={value.id} className="directory-related-order" href={`/orders/${value.id}`} navigate={navigate}>{value.number} · {value.generatedName}</NavigationLink>)}{!orders.some(value => value.customerId === id) && <p>No orders assigned.</p>}</div></article></div>}
    {order && <OrderMaterialsPanel orderId={order.id} client={client} navigate={navigate} />}
    {order && <article className="panel"><h2>Order data folder</h2><NasFolderReference path={order.folderPath} />
      {order.folderStatus && <p>Folder: {order.folderStatus.state.toLowerCase()}{order.folderStatus.error ? ` · ${order.folderStatus.error}` : ""}</p>}
      {canEdit && folderEnabled && (!order.folderPath || !order.folderNameMatches) && <><p>{order.folderPath ? "The saved properties now generate a different folder name. The existing folder keeps its current name until you confirm the rename." : "The order folder is waiting to be created. You can retry if it is still missing."}</p><button className="button" disabled={locked || ["PENDING", "RUNNING"].includes(order.folderStatus?.state ?? "")} onClick={() => setFolderAction(order.folderPath ? "RENAME" : "CREATE")}>{order.folderPath ? "Rename order folder…" : "Create order folder…"}</button></>}
      <dialog ref={dialog} aria-labelledby="order-folder-dialog-title" onCancel={event => { event.preventDefault(); if (!busy && !uncertain) { dialog.current?.close(); setFolderAction(null); } }}><h2 id="order-folder-dialog-title">{folderAction === "RENAME" ? "Rename order folder" : "Create order folder"}</h2><p>{folderAction === "RENAME" ? "This changes the folder name on the NAS and the path saved in this order. Confirm that this folder is not currently in use." : "This creates the order folder at the configured project location."}</p><dl className="info-list"><div><dt>Current folder</dt><dd>{order.folderPath ?? "Not created"}</dd></div><div><dt>New folder name</dt><dd>{order.generatedName}</dd></div></dl><div className="form-actions"><button className="button" disabled={busy || uncertain} onClick={() => { dialog.current?.close(); setFolderAction(null); }}>Cancel</button><button className="button button--primary" disabled={locked} onClick={() => { if (folderAction) { const action = folderAction; void submit({ run: key => directoryClient.folder(order, action, key), done: () => { dialog.current?.close(); refresh(); } }); } }}>Confirm {folderAction === "RENAME" ? "rename" : "create"}</button></div>{uncertain && <><p role="alert">{error}</p><button className="button" disabled={busy || requestGeneration !== sessionGeneration()} onClick={() => void submit()}>Retry same folder request</button></>}</dialog>
    </article>}
    {item && canEdit && item.sync?.enabled !== false && ["ERROR", "PENDING"].includes(item.sync?.state ?? "") && <button className="button" disabled={locked} onClick={() => void submit({ run: () => directoryClient.retrySync(kind === "customer" ? "CUSTOMER" : "ORDER", item.id), done: () => refresh() })}>Retry Notion synchronization</button>}
    {item && role === "ADMIN" && <DirectoryHistory kind={kind} id={item.id} updatedAt={item.updatedAt} />}
  </section>;
}
