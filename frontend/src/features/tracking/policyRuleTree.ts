import type { RuleNode } from "../strategies/historicalRules";

/** Wrap the policy's existing list without flattening independently bound events. */
export function policyRuleTree(
  conditions: RuleNode[],
  logic: string,
): RuleNode {
  return { [logic === "AND" ? "all" : "any"]: conditions };
}

export function policyGroupFromTree(tree: RuleNode): {
  conditions: RuleNode[];
  logic: "AND" | "OR";
} {
  if (Array.isArray(tree.all)) return { conditions: tree.all, logic: "AND" };
  if (Array.isArray(tree.any)) return { conditions: tree.any, logic: "OR" };
  return { conditions: [tree], logic: "AND" };
}
