import { lazy, Suspense } from "react";
import type { AgentChecks, ChartSpec } from "@/api/types";
import { AGENT_TEXT, type AgentLang } from "./labels";

const AgentChart = lazy(() => import("./AgentChart"));

/** The charts of one run, with the answer check's verdict on their values. */
export function AgentCharts({
  charts,
  checks,
  lang = "en",
}: {
  charts: ChartSpec[];
  checks?: AgentChecks | null;
  lang?: AgentLang;
}) {
  if (charts.length === 0) return null;
  return (
    <Suspense fallback={<div className="mt-3 h-64 animate-pulse rounded-lg bg-current/5" />}>
      {charts.map((spec) => {
        const unsupported = checks?.charts.find((c) => c.chart_id === spec.id)?.unsupported_values ?? [];
        return (
          <div key={spec.id}>
            <AgentChart spec={spec} lang={lang} />
            {unsupported.length > 0 && (
              <p className="mt-1 text-xs text-amber-700">
                {AGENT_TEXT.chartUnsupported[lang]}: {unsupported.join(", ")}
              </p>
            )}
          </div>
        );
      })}
    </Suspense>
  );
}
