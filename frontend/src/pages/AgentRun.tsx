import { AlertTriangle, Bot, CheckCircle2, ChevronDown, ChevronRight, Globe, Wrench, XCircle } from "lucide-react";
import { type ReactNode, useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import type { AgentChecks, AgentTrace, AgentTraceStep, Source } from "@/api/types";
import { AgentCharts } from "@/components/agent/AgentCharts";
import { formatMs } from "@/components/agent/AgentSteps";
import { toolLabel } from "@/components/agent/labels";
import { CitedAnswer, SourceList } from "@/components/CitedAnswer";
import { EmptyState, ErrorNote, PageHeader } from "@/components/common";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useAgentTrace } from "@/hooks/queries";
import { cn, formatDate } from "@/lib/utils";

/**
 * The agent inspector (PLAN_HYBRID.md Phase 6): one answer's run, rebuilt from its
 * stored trace only. Every model call and tool call in order, with arguments, the
 * result exactly as the model saw it (invariant 11), tokens and latency, then the
 * answer check (decision 0036).
 */
export default function AgentRun() {
  const traceId = Number(useParams().traceId);
  const trace = useAgentTrace(Number.isFinite(traceId) ? traceId : null);

  return (
    <div>
      <PageHeader
        title={`Agent run #${Number.isFinite(traceId) ? traceId : "?"}`}
        description="Every step Luna took for one answer, rebuilt from the stored trace."
      />
      <div className="mx-auto max-w-5xl space-y-6 px-8 py-6">
        <ErrorNote error={trace.error} />
        {trace.isLoading && <EmptyState>Loading the run…</EmptyState>}
        {trace.data && <Run trace={trace.data} />}
      </div>
    </div>
  );
}

const STATUS: Record<string, { label: string; variant: "success" | "warning" | "destructive" }> = {
  answered: { label: "Answered", variant: "success" },
  max_iterations: { label: "Hit the step limit", variant: "warning" },
  token_budget: { label: "Hit the token budget", variant: "warning" },
  tool_errors: { label: "Stopped on tool errors", variant: "warning" },
  llm_error: { label: "Model failed", variant: "destructive" },
};

function Run({ trace }: { trace: AgentTrace }) {
  const sources = useMemo(() => passageSources(trace.steps), [trace.steps]);
  const [highlighted, setHighlighted] = useState<number | null>(null);
  const toggle = (rank: number) => setHighlighted((h) => (h === rank ? null : rank));
  const status = STATUS[trace.status] ?? { label: trace.status, variant: "warning" as const };
  const tools = trace.steps.filter((s) => s.kind === "tool").length;

  return (
    <>
      <Card>
        <CardHeader>
          <CardTitle className="text-base">{trace.question}</CardTitle>
          <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
            <Badge variant={status.variant}>{status.label}</Badge>
            <span>{trace.model}</span>
            <span>·</span>
            <span>{formatDate(trace.created_at)}</span>
          </div>
        </CardHeader>
        <CardContent>
          <dl className="grid grid-cols-2 gap-4 sm:grid-cols-5">
            <Stat label="Model calls" value={trace.iterations} />
            <Stat label="Tool calls" value={tools} />
            <Stat label="Prompt tokens" value={trace.prompt_tokens.toLocaleString()} />
            <Stat label="Completion tokens" value={trace.completion_tokens.toLocaleString()} />
            <Stat label="Total time" value={formatMs(trace.duration_ms)} />
          </dl>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Timeline</CardTitle>
          <p className="text-sm text-muted-foreground">
            Bars show when each step ran within the {formatMs(trace.duration_ms)} run. Open a step to see what was
            sent and returned.
          </p>
        </CardHeader>
        <CardContent>
          <Timeline trace={trace} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Answer</CardTitle>
        </CardHeader>
        <CardContent>
          {trace.error && <ErrorNote error={trace.error} />}
          {trace.answer && (
            <CitedAnswer content={trace.answer} sources={sources} highlighted={highlighted} onCite={toggle} />
          )}
          <AgentCharts charts={trace.charts} checks={trace.checks} />
          <SourceList sources={sources} highlighted={highlighted} onSelect={toggle} />
        </CardContent>
      </Card>

      {trace.checks && <Checks checks={trace.checks} />}
    </>
  );
}

function Stat({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div>
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="text-lg font-semibold tabular-nums">{value}</dd>
    </div>
  );
}

// ------------------------------------------------------------------ timeline

function Timeline({ trace }: { trace: AgentTrace }) {
  // Steps run one after another; their offsets are the sum of the earlier ones.
  const total = Math.max(trace.duration_ms, trace.steps.reduce((sum, s) => sum + s.ms, 0), 1);
  let offset = 0;
  return (
    <ol className="space-y-2">
      {trace.steps.map((step, index) => {
        const start = offset;
        offset += step.ms;
        return (
          <StepRow
            key={`${index}-${step.kind}`}
            step={step}
            start={start / total}
            width={step.ms / total}
          />
        );
      })}
    </ol>
  );
}

function StepRow({ step, start, width }: { step: AgentTraceStep; start: number; width: number }) {
  const [open, setOpen] = useState(false);
  const failed = step.kind === "tool" && !step.ok;
  return (
    <li className={cn("rounded-lg border", failed && "border-destructive/40")}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex w-full items-center gap-3 px-3 py-2 text-left text-sm hover:bg-muted/50"
      >
        {open ? <ChevronDown className="size-4 shrink-0" /> : <ChevronRight className="size-4 shrink-0" />}
        <StepTitle step={step} />
        <span className="ml-auto hidden w-48 shrink-0 sm:block" aria-hidden>
          <span className="relative block h-2 rounded-full bg-muted">
            <span
              className={cn("absolute inset-y-0 rounded-full", step.kind === "llm" ? "bg-primary/70" : "bg-amber-500/80")}
              style={{ left: `${start * 100}%`, width: `max(${width * 100}%, 3px)` }}
            />
          </span>
        </span>
        <span className="w-16 shrink-0 text-right tabular-nums text-muted-foreground">{formatMs(step.ms)}</span>
      </button>
      {open && <div className="space-y-3 border-t px-4 py-3">{step.kind === "llm" ? <LLMDetail step={step} /> : <ToolDetail step={step} />}</div>}
    </li>
  );
}

function StepTitle({ step }: { step: AgentTraceStep }) {
  if (step.kind === "llm") {
    const asked = step.tool_calls.map((c) => c.name).join(", ");
    return (
      <span className="flex min-w-0 items-center gap-2">
        <Bot className="size-4 shrink-0 text-primary" />
        <span className="font-medium">Model call {step.iteration}</span>
        <span className="truncate text-muted-foreground">
          {asked ? `asks for ${asked}` : step.content.trim() ? "answers" : "empty reply"}
          {!step.tools_offered && " · final call, no tools"}
        </span>
        <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
          {step.prompt_tokens}+{step.completion_tokens} tok
        </span>
      </span>
    );
  }
  return (
    <span className="flex min-w-0 items-center gap-2">
      {step.ok ? <CheckCircle2 className="size-4 shrink-0 text-success" /> : <XCircle className="size-4 shrink-0 text-destructive" />}
      <Wrench className="size-3.5 shrink-0 text-muted-foreground" />
      <code className="font-mono font-medium">{step.name}</code>
      <span className="truncate text-muted-foreground">{toolLabel(step.name, "en")}</span>
      {step.reused && <Badge variant="secondary">earlier result reused</Badge>}
      {step.meta.external_call ? (
        <Badge variant="warning">
          <Globe className="mr-1 size-3" /> external call
        </Badge>
      ) : null}
    </span>
  );
}

function LLMDetail({ step }: { step: Extract<AgentTraceStep, { kind: "llm" }> }) {
  return (
    <>
      <Facts
        items={[
          ["Prompt tokens", step.prompt_tokens],
          ["Completion tokens", step.completion_tokens],
          ["Latency", formatMs(step.ms)],
          ["Tools offered", step.tools_offered ? "yes" : "no (final call)"],
        ]}
      />
      {step.parsed_from_content && (
        <p className="text-xs text-warning">The model wrote these tool calls as JSON in its text; the loop read them from there.</p>
      )}
      {step.content.trim() && (
        <Section title="Model text">
          <p className="whitespace-pre-wrap text-sm">{step.content}</p>
        </Section>
      )}
      {step.tool_calls.map((call, i) => (
        <Section key={`${i}-${call.name}`} title={`Requested: ${call.name}`}>
          <Json value={call.arguments} />
        </Section>
      ))}
    </>
  );
}

function ToolDetail({ step }: { step: Extract<AgentTraceStep, { kind: "tool" }> }) {
  const [raw, setRaw] = useState(false);
  const parsed = useMemo(() => parseJson(step.result), [step.result]);
  const external = step.meta.external_call;
  return (
    <>
      <Facts
        items={[
          ["Status", step.ok ? "ok" : "error"],
          ["Latency", formatMs(step.ms)],
          ["Iteration", step.iteration],
        ]}
      />
      <Section title="Arguments">
        <Json value={step.arguments} />
      </Section>
      {external ? (
        <Section title="External call (audited)">
          <Json value={external} />
        </Section>
      ) : null}
      <Section
        title="Result, as the model saw it"
        action={
          <button type="button" className="text-xs text-muted-foreground underline-offset-2 hover:underline" onClick={() => setRaw((r) => !r)}>
            {raw ? "Formatted" : "Exact text"}
          </button>
        }
      >
        {raw || parsed === undefined ? (
          <pre className="max-h-96 overflow-auto whitespace-pre-wrap rounded-md bg-muted p-3 font-mono text-xs">{step.result}</pre>
        ) : (
          <ResultView value={parsed} />
        )}
      </Section>
      {step.meta.passages && step.meta.passages.length > 0 && (
        <Section title="Passages behind the citation numbers">
          <ul className="space-y-1 text-xs">
            {step.meta.passages.map((p) => (
              <li key={p.number}>
                <span className="font-mono">[{p.number}]</span> {p.filename}
                {p.page ? `, page ${p.page}` : p.heading ? `, ${p.heading}` : ""} · chunk {p.chunk_id} · score{" "}
                {p.score.toFixed(3)}
              </li>
            ))}
          </ul>
        </Section>
      )}
    </>
  );
}

/** Rows become a table; other fields stay JSON. */
function ResultView({ value }: { value: unknown }) {
  if (!isRecord(value)) return <Json value={value} />;
  const { rows, passages, ...rest } = value;
  return (
    <div className="space-y-2">
      {Object.keys(rest).length > 0 && <Json value={rest} />}
      {Array.isArray(rows) && <RowsTable rows={rows} />}
      {Array.isArray(passages) && (
        <ol className="space-y-2">
          {passages.map((p, i) =>
            isRecord(p) ? (
              <li key={`${i}-${String(p.cite)}`} className="rounded-md bg-muted p-2 text-xs">
                <div className="font-medium">
                  {String(p.cite)} {String(p.document)}
                  {p.where ? <span className="text-muted-foreground"> · {String(p.where)}</span> : null}
                </div>
                <p className="mt-1 line-clamp-6 whitespace-pre-wrap">{String(p.text)}</p>
              </li>
            ) : null,
          )}
        </ol>
      )}
    </div>
  );
}

const ROW_PREVIEW = 25;

function RowsTable({ rows }: { rows: unknown[] }) {
  const [all, setAll] = useState(false);
  const records = rows.filter(isRecord);
  if (records.length === 0) return <p className="text-xs text-muted-foreground">No rows.</p>;
  const columns = [...new Set(records.flatMap((r) => Object.keys(r)))];
  const shown = all ? records : records.slice(0, ROW_PREVIEW);
  return (
    <div className="overflow-x-auto rounded-md border">
      <table className="w-full text-xs">
        <thead className="bg-muted">
          <tr>
            {columns.map((c) => (
              <th key={c} className="px-2 py-1.5 text-left font-medium">
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {shown.map((row, i) => (
            <tr key={i} className="border-t">
              {columns.map((c) => (
                <td key={c} className={cn("px-2 py-1", typeof row[c] === "number" && "text-right tabular-nums")}>
                  {cell(row[c])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      <p className="border-t px-2 py-1.5 text-xs text-muted-foreground">
        {records.length} row{records.length === 1 ? "" : "s"}
        {records.length > ROW_PREVIEW && (
          <button type="button" className="ml-2 underline-offset-2 hover:underline" onClick={() => setAll((a) => !a)}>
            {all ? `show the first ${ROW_PREVIEW}` : "show all"}
          </button>
        )}
      </p>
    </div>
  );
}

// ------------------------------------------------------------------ checks

const CITATIONS: Record<string, string> = {
  ok: "Every citation points at a passage the run showed.",
  missing: "The answer cites no passage although the documents were searched.",
  invalid: "A citation number points at no passage shown in this run.",
  not_applicable: "No documents were searched, so no citations were needed.",
};

function Checks({ checks }: { checks: AgentChecks }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Answer check</CardTitle>
        <p className="text-sm text-muted-foreground">
          Every number must come from a tool result (invariant 12); document facts cite a passage.
        </p>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="flex items-start gap-2 text-sm">
          {checks.citation_status === "ok" || checks.citation_status === "not_applicable" ? (
            <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-success" />
          ) : (
            <AlertTriangle className="mt-0.5 size-4 shrink-0 text-warning" />
          )}
          <span>
            <span className="font-medium">Citations: {checks.citation_status}.</span> {CITATIONS[checks.citation_status]}
          </span>
        </p>
        {checks.numbers.length > 0 ? (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b text-left text-xs text-muted-foreground">
                <th className="py-1.5 pr-4 font-medium">Number in the answer</th>
                <th className="py-1.5 font-medium">Found in</th>
              </tr>
            </thead>
            <tbody>
              {checks.numbers.map((claim, i) => (
                <tr key={`${i}-${claim.text}`} className="border-b last:border-0">
                  <td className="py-1.5 pr-4 font-mono tabular-nums">{claim.text}</td>
                  <td className="py-1.5">
                    {claim.sources.length ? (
                      claim.sources.join(", ")
                    ) : (
                      <span className="inline-flex items-center gap-1 text-destructive">
                        <AlertTriangle className="size-3.5" /> not in any tool result
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="text-sm text-muted-foreground">The answer states no numbers.</p>
        )}
        {checks.charts.map((c) => (
          <p key={c.chart_id} className="text-sm">
            Chart <code className="font-mono">{c.chart_id}</code>: {c.values} values,{" "}
            {c.unsupported_values.length ? (
              <span className="text-destructive">not in any tool result: {c.unsupported_values.join(", ")}</span>
            ) : (
              "all from tool results"
            )}
            .
          </p>
        ))}
      </CardContent>
    </Card>
  );
}

// ------------------------------------------------------------------ helpers

function Facts({ items }: { items: [string, ReactNode][] }) {
  return (
    <dl className="flex flex-wrap gap-x-6 gap-y-1 text-xs">
      {items.map(([label, value]) => (
        <div key={label} className="flex gap-1.5">
          <dt className="text-muted-foreground">{label}</dt>
          <dd className="font-medium tabular-nums">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

function Section({ title, action, children }: { title: string; action?: ReactNode; children: ReactNode }) {
  return (
    <section>
      <div className="mb-1 flex items-center justify-between">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">{title}</h3>
        {action}
      </div>
      {children}
    </section>
  );
}

function Json({ value }: { value: unknown }) {
  return (
    <pre className="max-h-80 overflow-auto whitespace-pre-wrap rounded-md bg-muted p-3 font-mono text-xs">
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}

function cell(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "number") return value.toLocaleString(undefined, { maximumFractionDigits: 4 });
  return typeof value === "object" ? JSON.stringify(value) : String(value);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function parseJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return undefined;
  }
}

/** Every passage the run showed, as citation sources: "[3]" is rank 3. */
function passageSources(steps: AgentTraceStep[]): Source[] {
  const byNumber = new Map<number, Source>();
  for (const step of steps) {
    if (step.kind !== "tool" || !step.ok) continue;
    for (const p of step.meta.passages ?? []) {
      if (!byNumber.has(p.number)) {
        byNumber.set(p.number, {
          rank: p.number,
          chunk_id: p.chunk_id,
          score: p.score,
          document_id: p.document_id,
          filename: p.filename,
          page: p.page,
          heading: p.heading,
        });
      }
    }
  }
  return [...byNumber.values()].sort((a, b) => a.rank - b.rank);
}
