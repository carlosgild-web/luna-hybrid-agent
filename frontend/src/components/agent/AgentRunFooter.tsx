import { ArrowUpRight, Wrench } from "lucide-react";
import { Link } from "react-router-dom";
import { useAgentTrace } from "@/hooks/queries";
import { AgentCharts } from "./AgentCharts";
import { AGENT_TEXT, type AgentLang, toolLabel } from "./labels";

/** A saved agent answer's charts, from its stored trace. */
export function AgentRunCharts({ traceId, lang = "en" }: { traceId: number; lang?: AgentLang }) {
  const trace = useAgentTrace(traceId);
  return trace.data ? <AgentCharts charts={trace.data.charts} checks={trace.data.checks} lang={lang} /> : null;
}

/** Under a saved agent answer: the tools it used and the inspector link
 * (and its charts, unless the caller shows them with `AgentRunCharts`). */
export function AgentRunFooter({
  traceId,
  lang = "en",
  newTab = false,
  charts = true,
}: {
  traceId: number;
  lang?: AgentLang;
  /** The portal opens the console's inspector in a new tab. */
  newTab?: boolean;
  charts?: boolean;
}) {
  const trace = useAgentTrace(traceId);
  const tools = [
    ...new Set((trace.data?.steps ?? []).flatMap((s) => (s.kind === "tool" ? [s.name] : []))),
  ];
  return (
    <div>
      {charts && trace.data && <AgentCharts charts={trace.data.charts} checks={trace.data.checks} lang={lang} />}
      <div className="mt-2 flex flex-wrap items-center gap-1.5 text-xs opacity-80">
        {tools.map((name) => (
          <span key={name} className="inline-flex items-center gap-1 rounded-full border border-current/15 px-2 py-0.5" title={name}>
            <Wrench className="size-3 opacity-50" aria-hidden />
            {toolLabel(name, lang)}
          </span>
        ))}
        <InspectLink traceId={traceId} lang={lang} newTab={newTab} />
      </div>
    </div>
  );
}

export function InspectLink({ traceId, lang = "en", newTab = false }: { traceId: number; lang?: AgentLang; newTab?: boolean }) {
  const label = (
    <>
      {AGENT_TEXT.inspect[lang]} <ArrowUpRight className="size-3" aria-hidden />
    </>
  );
  const className = "inline-flex items-center gap-0.5 font-medium underline-offset-2 hover:underline";
  return newTab ? (
    <a href={`/agent/${traceId}`} target="_blank" rel="noreferrer" className={className}>
      {label}
    </a>
  ) : (
    <Link to={`/agent/${traceId}`} className={className}>
      {label}
    </Link>
  );
}
