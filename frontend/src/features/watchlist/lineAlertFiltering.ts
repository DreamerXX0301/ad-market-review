import type { Row } from "../../lib/api";
import { makeFuzzyMatcher } from "../../lib/fuzzySearch";
import {
  lineAlertInScope,
  lineAlertSource,
  lineAlertView,
} from "./lineAlertPresentation";

export function filterLineAlerts(
  events: Row[],
  query: string,
  state: string,
  asOf?: string,
) {
  const matches = makeFuzzyMatcher(query);
  return events.filter(
    (event) =>
      lineAlertInScope(event, asOf) &&
      (state === "all" ||
        (state === "invalidated") === lineAlertView(event).invalidated) &&
      matches(
        event.name,
        event.code,
        event.instrument_id,
        event.title,
        event.reason,
        lineAlertView(event).title,
        lineAlertSource(event),
      ),
  );
}
