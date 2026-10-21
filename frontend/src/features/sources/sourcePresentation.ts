type Capability = Record<string, any>;
export type CapabilityState = "verified" | "denied" | "unknown" | "unsupported";

export const capabilityStateLabels: Record<CapabilityState, string> = {
  verified: "本次验证可用",
  denied: "接口拒绝访问",
  unknown: "待核验",
  unsupported: "尚未接入",
};

/** Keep an explicit negative/unknown result ahead of legacy availability flags. */
export function capabilityState(capability: Capability): CapabilityState {
  const status = String(capability.status || "").toLowerCase();
  if (["locked", "denied", "permission_denied"].includes(status))
    return "denied";
  if (status === "unsupported") return "unsupported";
  if (["available", "ready", "verified", "success", "allowed"].includes(status))
    return "verified";
  if (status) return "unknown";
  if (capability.account_access === "denied") return "denied";
  if (capability.account_access === "allowed" || capability.available === true)
    return "verified";
  return "unknown";
}

export function sourceCapabilities(source: Capability): Capability[] {
  if (Array.isArray(source.capabilities)) return source.capabilities;
  return Object.entries(source.capabilities || {}).map(([key, value]) => ({
    key,
    ...(value && typeof value === "object"
      ? (value as Capability)
      : { status: value === true ? "available" : "unknown" }),
  }));
}

export function capabilityCounts(capabilities: Capability[]) {
  const counts = { verified: 0, denied: 0, unknown: 0, unsupported: 0 };
  for (const capability of capabilities)
    counts[capabilityState(capability)] += 1;
  return counts;
}
