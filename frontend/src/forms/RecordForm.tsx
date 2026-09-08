import { useEffect, useLayoutEffect, useRef, useState, type FormEvent } from "react";
import { ApiError } from "../api/errors";
import { NavigationLink } from "../components/NavigationLink";
import { validate, type Field, type Values } from "./fields";

export interface FormDefinition {
  title: string;
  fields: Field[];
  initial: Values;
  cancel: string;
  save: (values: Values) => Promise<{ path: string; message: string }>;
}
export function RecordForm({
  definition,
  navigate,
  onSaved,
}: {
  definition: FormDefinition;
  navigate: (path: string) => void;
  onSaved: (path: string, message: string) => void;
}) {
  const [values, setValues] = useState(definition.initial);
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
    return () => {
      active.current = false;
    };
  }, []);
  useEffect(() => {
    if (failure) summary.current?.focus();
  }, [failure]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (locked.current) return;
    const errors = validate(definition.fields, values);
    if (Object.keys(errors).length) {
      setFailure({
        message: "Please correct the highlighted fields.",
        fields: errors,
      });
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
        <fieldset disabled={saving}>
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
                      <option key={option.value} value={option.value}>
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
            disabled={saving}
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
