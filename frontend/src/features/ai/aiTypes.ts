export type AIKind = "stock" | "market";
export type AIItem = Record<string, any>;
export interface AIProvider {
  provider_id: string;
  name: string;
  configured: boolean;
  enabled: boolean;
  model: string;
  config_revision: number;
  verification?: {
    state: string;
    items?: AIItem[];
    checked_at_ms?: number;
    task_id?: string;
  };
  models?: (string | { id: string; name?: string })[];
  discovered_models?: (string | { id: string; name?: string })[];
  limits?: {
    daily_budget_usd: string;
    daily_request_limit: number;
    timeout_seconds: number;
  };
}
export interface AIContext extends AIItem {
  kind?: AIKind | "stock_research" | "market_review";
  subject?: string | AIItem;
  instrument_id?: string;
  as_of?: string;
  data_cutoff?: string;
  evidence?: AIItem[];
  claims?: AIItem[];
  metrics?: AIItem[];
  gaps?: AIItem[];
}
export interface AISession extends AIItem {
  session_id: string;
  kind: AIKind;
  provider_id: string;
  model: string;
  context_digest: string;
  context?: AIContext;
  keep: boolean;
  created_at_ms: number;
  updated_at_ms: number;
  expires_at_ms?: number;
  mode?: "local" | "web";
  allow_web?: boolean;
}
export interface AIWebSource {
  source_id: string;
  url: string;
  title: string;
  published_at?: string | number | null;
  accessed_at_ms?: number;
}
export interface AIWebCitation {
  start_index: number;
  end_index: number;
  url: string;
  title: string;
  source_id: string;
}
export interface AIMessage extends AIItem {
  message_id: string;
  role: string;
  text: string;
  interpretation?: AIItem;
  created_at_ms: number;
  state: string;
  response_type?: string;
  web?: {
    allowed: boolean;
    used: boolean;
    search_call_count: number;
    queried_at_ms: number;
    requested_as_of: string;
    validation: string;
  };
  citations?: AIWebCitation[];
  sources?: AIWebSource[];
  source_context?: AIItem;
  tool_traces?: AIItem[];
  partial?: boolean;
}
export interface AITask extends AIItem {
  task_id: string;
  state: string;
  reason_code?: string;
  message?: string;
}
