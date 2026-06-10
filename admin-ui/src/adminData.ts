// Small data helpers for ordering and compacting admin API payloads.

import type { LongService } from "./types";

export const QUOTES = [
  "The King counts every task twice: once for courage, once for evidence.",
  "A proper goblin returns receipts.",
  "No mystery enters the queue without a lantern tied to it.",
  "The throne accepts chaos, but only with structured JSON.",
  "If it moves, heartbeat it. If it stops, write it down.",
  "Tiny workers, enormous paperwork.",
];

export function latestFirst<T>(items: T[]): T[] {
  return [...items].sort((left, right) => {
    const leftRecord = left as Record<string, unknown>;
    const rightRecord = right as Record<string, unknown>;
    const leftDate = leftRecord.created_at || leftRecord.submitted_at || leftRecord.started_at;
    const rightDate = rightRecord.created_at || rightRecord.submitted_at || rightRecord.started_at;
    const leftTime = typeof leftDate === "string" ? Date.parse(leftDate) : 0;
    const rightTime = typeof rightDate === "string" ? Date.parse(rightDate) : 0;
    return (Number.isNaN(rightTime) ? 0 : rightTime) - (Number.isNaN(leftTime) ? 0 : leftTime);
  });
}

function serviceRank(service: LongService): number {
  if (service.status === "stopped") return 2;
  if (service.status === "failed") return 1;
  return 0;
}

export function usefulServicesFirst(services: LongService[]): LongService[] {
  return latestFirst(services).sort((left, right) => serviceRank(left) - serviceRank(right));
}

export function quoteFor(seed: number) {
  return QUOTES[seed % QUOTES.length];
}

export async function readJson(response: Response) {
  const text = await response.text();
  if (!text) return null;
  return JSON.parse(text);
}

export function compactTrafficPayload(value: unknown, depth = 0): unknown {
  if (depth > 3) return "[truncated]";
  if (Array.isArray(value)) {
    const preview = value.slice(0, 3).map((item) => compactTrafficPayload(item, depth + 1));
    return value.length > 3 ? [...preview, `... ${value.length - 3} more`] : preview;
  }
  if (value && typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>);
    const compacted = Object.fromEntries(
      entries.slice(0, 12).map(([key, item]) => [key, compactTrafficPayload(item, depth + 1)]),
    );
    if (entries.length > 12) compacted._truncated = `${entries.length - 12} more fields`;
    return compacted;
  }
  return value;
}
