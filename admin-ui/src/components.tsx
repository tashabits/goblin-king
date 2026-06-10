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
