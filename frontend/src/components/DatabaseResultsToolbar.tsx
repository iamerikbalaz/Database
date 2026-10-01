import type { ReactNode } from "react";
import { databaseSortOptions } from "./useDatabaseFilters";
import "./DatabaseResultsToolbar.css";

/** Results count, view options and sorting remain available outside the filters. */
export function DatabaseResultsToolbar({ count, sort, onSortChange, sortLabel, disabled, options = databaseSortOptions, children }: {
  count: ReactNode;
  sort: string;
  onSortChange: (value: string) => void;
  sortLabel: string;
  disabled?: boolean;
  options?: ReadonlyArray<{ value: string; label: string }>;
  children?: ReactNode;
}) {
  return <div className="database-results-toolbar">
    <span className="database-results-count" aria-live="polite">{count}</span>
    <div className="database-results-controls">
      {children}
      <label className="database-results-sort"><span aria-hidden="true">Sort</span>
        <select aria-label={sortLabel} value={sort} disabled={disabled} onChange={event => onSortChange(event.target.value)}>
          {options.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
        </select>
      </label>
    </div>
  </div>;
}
