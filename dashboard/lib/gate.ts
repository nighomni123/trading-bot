/**
 * Epistemic gate — pipeline topology, node states, and research blockers.
 *
 * Numbers here are read from artifacts in lib/ modules at render time, not
 * hardcoded, except for qualitative verdicts which are the author's judgment.
 */

export type NodeState = "pass" | "fail" | "locked" | "warn";

export type PipelineNode = {
  id: string;
  label: string;
  stage: string;
  state: NodeState;
  /** Headline number rendered under the node. */
  metric: string;
  metricLabel: string;
  /** Tooltip / detail text. */
  detail: string;
  /** Where the numbers came from. */
  artifact?: string;
};

export type Blocker = {
  id: string;
  title: string;
  detail: string;
  severity: "blocker" | "watch";
  artifact: string;
};

export const NODE_TONE: Record<NodeState, string> = {
  pass: "#2ea043",
  fail: "#f85149",
  locked: "#6e7681",
  warn: "#d29922",
};
