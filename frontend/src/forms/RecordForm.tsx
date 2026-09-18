import { useEffect, useLayoutEffect, useRef, useState, type FormEvent } from "react";
import { ApiError } from "../api/errors";
import { NavigationLink } from "../components/NavigationLink";
import { validate, type Field, type Values } from "./fields";
import { useSession } from "../auth/context";
import { useRecordCommand, type RecordCommandDefinition } from "./useRecordCommand";
import { PendingRecordSave } from "./PendingRecordSave";

export interface FormDefinition {
  title: string;
  fields: Field[];
  initial: Values;
  cancel: string;
  command?: RecordCommandDefinition;
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
  const [localSaving, setLocalSaving] = useState(false);
  const [failure, setFailure] = useState<{
    message: string;
    fields: Record<string, string>;
  } | null>(null);
  const controller = useRecordCommand({ actorId, command: definition.command, save: definition.save,
    onStart: () => setFailure(null), onSaved: (result) => onSaved(result.path, result.message),
    onFailure: ({ message, issues }) => {
      const fields: Record<string, string> = {};
      for (const issue of issues) {
        const field = definition.fields.find((item) => item.apiName === issue.field);
        if (field) fields[field.name] = issue.message;
      }
      setFailure({ message, fields });
    },
  });
  const { packet, ownPacket } = controller;
  const [values, setValues] = useState(ownPacket?.values ?? definition.initial);
  const saving = localSaving || controller.busy;
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
    return () => {
      active.current = false;
    };
  }, []);
  useLayoutEffect(() => {
    if (failure) summary.current?.focus();
  }, [failure]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (locked.current || packet || controller.busy) return;
    const errors = validate(definition.fields, values);
    if (Object.keys(errors).length) {
      setFailure({
        message: "Please correct the highlighted fields.",
        fields: errors,
      });
      return;
    }
    if (definition.command) {
      await controller.submit(values);
      return;
    }
    locked.current = true;
    setLocalSaving(true);
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
        setLocalSaving(false);
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
        <PendingRecordSave controller={controller} />
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
