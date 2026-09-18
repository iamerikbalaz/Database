import { useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore, type FormEvent } from "react";
import { ApiError } from "../api/errors";
import { NavigationLink } from "../components/NavigationLink";
import { validate, type Field, type Values } from "./fields";
import { useSession } from "../auth/context";
import { resourceCommandClient, resourceRequestHash, type CommandScope } from "../api/resourceCommandClient";
import { pendingRecordCommands, type PendingRecordCommand } from "./pendingRecordCommands";

export interface FormDefinition {
  title: string;
  fields: Field[];
  initial: Values;
  cancel: string;
  command?: CommandScope & { payload: (values: Values) => object };
  save: (values: Values, key?: string) => Promise<{ path: string; message: string }>;
}
type Props = {
  definition: FormDefinition;
  navigate: (path: string) => void;
  onSaved: (path: string, message: string) => void;
};
export function RecordForm(props: Props) {
  const actorId = useSession()?.session.user.id;
  return <RecordFormWork key={`${actorId ?? "anonymous"}:${props.definition.command?.editorPath ?? props.definition.cancel}`} {...props} actorId={actorId} />;
}
function RecordFormWork({
  definition,
  navigate,
  onSaved,
  actorId,
}: Props & { actorId?: string }) {
  const owner = useRef(actorId ?? Symbol("unmounted form"));
  const packet = useSyncExternalStore(pendingRecordCommands.subscribe, () => pendingRecordCommands.get(owner.current));
  const ownPacket = packet && packet.scope.editorPath === definition.command?.editorPath ? packet : null;
  const [values, setValues] = useState(ownPacket?.values ?? definition.initial);
  const [saving, setSaving] = useState(false);
  const [failure, setFailure] = useState<{
    message: string;
    fields: Record<string, string>;
  } | null>(null);
  const locked = useRef(false);
  const active = useRef(true);
  const firstField = useRef<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>(null);
  const firstFieldName = definition.fields.find((field) => !field.disabled)?.name;
  const summary = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    // Establish focus in the mount commit, before paint or DOM observers.
    // Run only on mount so later renders cannot steal focus from the summary.
    firstField.current?.focus();
  }, []);
  useEffect(() => {
    active.current = true;
    const storageOwner = owner.current;
    return () => {
      active.current = false;
      if (!actorId) pendingRecordCommands.set(storageOwner, null);
    };
  }, [actorId]);
  useLayoutEffect(() => {
    if (failure) summary.current?.focus();
  }, [failure]);

  function finish(result: { path: string; message: string }) {
    pendingRecordCommands.set(owner.current, null);
    if (active.current) onSaved(result.path, result.message);
  }
  async function sendPacket(current: PendingRecordCommand, recover = false) {
    const stored = pendingRecordCommands.get(owner.current);
    if (locked.current || stored?.phase === "SENDING" || (stored && stored.key !== current.key)) return;
    locked.current = true; setSaving(true); setFailure(null);
    pendingRecordCommands.set(owner.current, { ...current, phase: "SENDING" });
    try {
      if (recover && !actorId) throw new Error("An authenticated account is required");
      const result = recover ? await resourceCommandClient.recover(current.scope, actorId!, current.key, current.requestHash) : await current.save(current.values, current.key);
      // A late success stays available to a remounted form; it never navigates
      // whichever unrelated page/account is now on screen.
      if (active.current) finish(result);
      else if (actorId) pendingRecordCommands.set(owner.current, { ...current, phase: "SAVED", result });
    } catch (error) {
      const rejected = !current.wasUnknown && !recover && error instanceof ApiError && error.status >= 400 && error.status < 500;
      if (actorId || active.current) pendingRecordCommands.set(owner.current, rejected ? null : { ...current, phase: "UNKNOWN", wasUnknown: true });
      if (active.current) {
        const fields: Record<string, string> = {};
        if (rejected && error instanceof ApiError) for (const issue of error.issues) {
          const field = definition.fields.find((item) => item.apiName === issue.field);
          if (field) fields[field.name] = issue.message;
        }
        setFailure({ fields, message: rejected && error instanceof ApiError ? error.message :
          "The save outcome is unknown. Check the saved result or retry this exact request before making another change." });
      }
    } finally {
      locked.current = false;
      if (active.current) setSaving(false);
    }
  }
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (locked.current || pendingRecordCommands.get(owner.current)) return;
    const errors = validate(definition.fields, values);
    if (Object.keys(errors).length) {
      setFailure({
        message: "Please correct the highlighted fields.",
        fields: errors,
      });
      return;
    }
    if (definition.command) {
      locked.current = true; setSaving(true); setFailure(null);
      const { payload, ...scope } = definition.command;
      let prepared: PendingRecordCommand;
      try {
        prepared = { key: crypto.randomUUID(), requestHash: await resourceRequestHash(scope, payload(values)), scope, values: { ...values }, save: definition.save,
          phase: "UNKNOWN", wasUnknown: false };
      } catch {
        if (active.current) setFailure({ message: "The request could not be prepared. No save was sent. Reload this form and try again.", fields: {} });
        return;
      } finally { locked.current = false; if (active.current) setSaving(false); }
      if (active.current) await sendPacket(prepared);
      return;
    }
    locked.current = true;
    setSaving(true);
    setFailure(null);
    try {
      const result = await definition.save(values);
      if (active.current) onSaved(result.path, result.message);
    } catch (error) {
      if (!active.current) return;
      const fields: Record<string, string> = {};
      if (error instanceof ApiError) {
        for (const issue of error.issues) {
          const field = definition.fields.find(
            (f) => f.apiName === issue.field,
          );
          if (field) fields[field.name] = issue.message;
        }
      }
      setFailure({
        message:
          error instanceof ApiError
            ? error.message
            : "Saving failed. Check your connection and try again.",
        fields,
      });
    } finally {
      if (active.current) {
        locked.current = false;
        setSaving(false);
      }
    }
  }
  return (
    <section>
      <div className="page-heading">
        <div>
          <h1>{definition.title}</h1>
          <p>Fields marked * are required.</p>
        </div>
      </div>
      <form
        className="panel record-form"
        aria-label={definition.title}
        noValidate
        onSubmit={submit}
        aria-busy={saving}
      >
        {failure && (
          <div ref={summary} role="alert" tabIndex={-1} className="form-error">
            <p>{failure.message}</p>
            {Object.keys(failure.fields).length > 0 && (
              <ul>
                {Object.entries(failure.fields).map(([name, message]) => (
                  <li key={name}>
                    <a
                      href={"#field-" + name}
                      onClick={(event) => {
                        event.preventDefault();
                        document.getElementById("field-" + name)?.focus();
                      }}
                    >
                      {message}
                    </a>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
        {packet && !ownPacket && <p>An unresolved save belongs to another form. <button type="button" className="button" onClick={() => navigate(packet.scope.editorPath)}>Open pending form</button></p>}
        {ownPacket && <section aria-label="Pending save">
          <p>Your submitted values are kept for this request. Leaving or reloading the application can lose this recovery information.</p>
          <p className="revision-hash">Request: {ownPacket.key}</p>
          {ownPacket.phase === "UNKNOWN" && <div className="form-actions">
            <button type="button" className="button" disabled={saving} onClick={() => void sendPacket(ownPacket, true)}>Check saved result</button>
            <button type="button" className="button" disabled={saving} onClick={() => void sendPacket(ownPacket)}>Retry exact save</button>
          </div>}
          {ownPacket.phase === "SAVED" && ownPacket.result && <button type="button" className="button" onClick={() => finish(ownPacket.result!)}>Open saved record</button>}
          {ownPacket.phase === "SENDING" && <p role="status">Checking this save…</p>}
        </section>}
        <fieldset disabled={saving || packet !== null}>
          <legend className="sr-only">{definition.title}</legend>
          {definition.fields.map((field) => {
            const id = "field-" + field.name;
            const error = failure?.fields[field.name];
            const props = {
              ref: field.name === firstFieldName
                ? (element: HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement | null) => {
                    firstField.current = element;
                  }
                : undefined,
              id,
              name: field.name,
              required: field.required,
              disabled: field.disabled,
              "aria-invalid": !!error,
              "aria-describedby": error ? id + "-error" : undefined,
            };
            return (
              <div key={field.name} className="form-field">
                <label htmlFor={id}>
                  {field.label}
                  {field.required ? " *" : ""}
                </label>
                {field.type === "select" ? (
                  <select
                    {...props}
                    value={values[field.name]}
                    onChange={(e) =>
                      setValues({ ...values, [field.name]: e.target.value })
                    }
                  >
                    <option value="">Select {field.label.toLowerCase()}</option>
                    {field.options?.map((option) => (
                      <option key={option.value} value={option.value} disabled={option.disabled}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                ) : field.type === "textarea" ? (
                  <textarea
                    {...props}
                    rows={4}
                    value={values[field.name]}
                    onChange={(e) =>
                      setValues({ ...values, [field.name]: e.target.value })
                    }
                  />
                ) : field.type === "checkbox" ? (
                  <input
                    {...props}
                    type="checkbox"
                    checked={values[field.name] === "true"}
                    onChange={(e) =>
                      setValues({
                        ...values,
                        [field.name]: String(e.target.checked),
                      })
                    }
                  />
                ) : (
                  <input
                    {...props}
                    type={field.type ?? "text"}
                    value={values[field.name]}
                    onChange={(e) =>
                      setValues({ ...values, [field.name]: e.target.value })
                    }
                  />
                )}
                {error && (
                  <p id={id + "-error"} className="field-error">
                    {error}
                  </p>
                )}
              </div>
            );
          })}
        </fieldset>
        <div className="form-actions">
          <button
            className="button button--primary"
            disabled={saving || packet !== null}
            type="submit"
          >
            {saving ? "Saving…" : "Save"}
          </button>
          {!saving && (
            <NavigationLink
              className="button"
              href={definition.cancel}
              navigate={navigate}
            >
              Cancel
            </NavigationLink>
          )}
        </div>
        <p role="status">{saving ? "Saving your changes…" : ""}</p>
      </form>
    </section>
  );
}
