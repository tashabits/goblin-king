// Reusable presentational pieces for compact admin panels.

import type { ReactNode } from "react";

import { quoteFor } from "./adminData";
import type { TableRow } from "./types";

export function Stat({ icon, label, value }: { icon: ReactNode; label: string; value: number }) {
  return (
    <article className="stat">
      {icon}
      <span>{label}</span>
      <strong>{value}</strong>
    </article>
  );
}

export function Table({ title, rows }: { title: string; rows: TableRow[] }) {
  return (
    <div className="table-wrap">
      <h3>{title}</h3>
      {rows.length === 0 ? (
        <p className="empty">"{quoteFor(title.length)}"</p>
      ) : (
        <table>
          <tbody>
            {rows.map((row, index) => (
              <tr key={`${title}-${index}`}>
                {row.map((cell, cellIndex) => <td key={cellIndex}>{cell}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

export function EffectivePolicy({
  policy,
  emptyLabel = "No effective policy recorded.",
}: {
  policy?: Record<string, unknown> | null;
  emptyLabel?: string;
}) {
  const hasPolicy = policy && Object.keys(policy).length > 0;

  return (
    <div className="effective-policy" aria-label="Effective policy">
      <h4>Effective policy</h4>
      {hasPolicy ? (
        <pre>{JSON.stringify(policy, null, 2)}</pre>
      ) : (
        <p className="muted">{emptyLabel}</p>
      )}
    </div>
  );
}
