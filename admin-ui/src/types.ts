// Shared API-facing shapes used by the React admin panels.
import type { ReactNode } from "react";

export type Goblin = {
  kind: string;
  display_name: string;
  worker_image?: string | null;
  worker_mapped: boolean;
  source?: string;
  validation_status?: {
    state: "validated" | "failed" | "stale" | "unknown";
    message: string;
    image?: string | null;
    image_digest?: string | null;
    contract_version?: string | null;
    validator_version?: string | null;
    validated_at?: string | null;
    failure_reasons?: string[];
  };
};

export type Job = {
  id: string;
  kind: string;
  status: string;
  input: Record<string, unknown>;
  created_at: string;
  due_at?: string | null;
  last_error?: string | null;
};

export type Run = {
  id: string;
  job_id: string;
  kind: string;
  status: string;
  result?: Record<string, unknown> | null;
  error?: string | null;
  started_at: string;
  resource_policy?: Record<string, unknown> | null;
};

export type EventRecord = {
  id: string;
  event_type: string;
  source: string;
  created_at: string;
  payload: Record<string, unknown>;
};

export type EventStreamStatus = {
  stream: string;
  ok: boolean;
  length: number;
  last_generated_id?: string | null;
  groups: Record<string, unknown>[];
  pending: number;
  error?: string | null;
};

export type Heartbeat = {
  owner_id: string;
  owner_type: string;
  status: string;
  last_seen_at: string;
};

export type LongService = {
  id: string;
  kind: string;
  status: string;
  base_url: string;
  last_probe_json?: Record<string, unknown> | null;
};

export type AuditLog = {
  id: string;
  action: string;
  outcome: string;
  created_at: string;
};

export type Schedule = {
  id: string;
  kind: string;
  cron: string;
  enabled: boolean;
  next_run_at: string;
};

export type FanoutDetail = {
  status: string;
  fanout: { id: string; description?: string | null };
  counts: Record<string, number>;
};

export type TrafficEntry = {
  label: string;
  request: unknown;
  response: unknown;
};

export type AdminConfig = {
  deploymentScope: string;
  longHelloUrl: string;
};

export type CleanupResponse = {
  dry_run: boolean;
  deleted: boolean;
  counts: Record<string, number>;
};

export type ArtifactStorageStatus = {
  root: string;
  exists: boolean;
  writable: boolean;
  file_count: number;
  total_bytes: number;
  metadata_count: number;
};

export type ArtifactCleanupResponse = {
  dry_run: boolean;
  deleted: boolean;
  root: string;
  files_selected: number;
  bytes_selected: number;
  files: string[];
};

export type DiscoveryStatus = {
  active_goblin_count: number;
  worker_mapped_count: number;
  worker_unmapped: string[];
  discovery_version: number;
  last_successful_reload_at: string;
  last_failed_reload_at?: string | null;
  last_error?: string | null;
};

export type DiscoverySources = {
  project_settings?: string | null;
  registry_files: string[];
  entry_points_enabled: boolean;
  worker_image_map: string;
  goblin_kinds: string[];
  worker_mapped_kinds: string[];
  worker_unmapped_kinds: string[];
  rejected_definitions: string[];
  duplicate_kind_errors: string[];
};

export type ImagePromotion = {
  id: string;
  kind: string;
  source_image: string;
  target_image: string;
  status: string;
  digest?: string | null;
  created_at: string;
  detail: Record<string, unknown>;
};

export type DeploymentRecord = {
  id: string;
  name: string;
  action: string;
  status: string;
  command: string[];
  output?: string | null;
  created_at: string;
  detail: Record<string, unknown>;
};

export type TableRow = ReactNode[];
