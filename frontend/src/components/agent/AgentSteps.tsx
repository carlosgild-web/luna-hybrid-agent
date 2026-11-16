import { Check, Loader2, Wrench, X } from "lucide-react";
import type { AgentStepEvent } from "@/api/types";
import { cn } from "@/lib/utils";
import { AGENT_TEXT, type AgentLang, toolLabel } from "./labels";

interface Chip {
  name: string;
  arguments: Record<string, unknown>;
  state: "running" | "ok" | "failed";
  reused: boolean;
  ms: number | null;
}

/** Turns the streamed steps into one chip per tool call. A model step that asks
 * for tools opens their chips ("🔧 sales_report…"); each tool step closes one. */
export function toolChips(steps: AgentStepEvent[]): Chip[] {
  const chips: Chip[] = [];
  for (const step of steps) {
    if (step.kind === "llm") {
      for (const call of step.tool_calls) {
        chips.push({ name: call.name, arguments: call.arguments, state: "running", reused: false, ms: null });
      }
    } else {
      const open = chips.find((c) => c.state === "running" && c.name === step.name);
      const done = { state: step.ok ? "ok" : "failed", reused: step.reused, ms: step.ms } as const;
      if (open) Object.assign(open, done);
      else chips.push({ name: step.name, arguments: step.arguments, ...done });
    }
  }
  return chips;
}

/** The run so far, as it happens: each tool call, then what Luna is doing now. */
export function AgentSteps({
  steps,
  running,
  hasAnswer,
  lang = "en",
  className,
}: {
  steps: AgentStepEvent[];
  running: boolean;
  hasAnswer: boolean;
  lang?: AgentLang;
  className?: string;
}) {
  const chips = toolChips(steps);
  const toolsRunning = chips.some((c) => c.state === "running");
  const answered = steps.some((s) => s.kind === "llm" && s.answered);
  if (!running && chips.length === 0) return null;

  return (
    <div className={cn("space-y-1.5", className)}>
      {chips.length > 0 && (
        <ol className="flex flex-wrap gap-1.5" aria-label={AGENT_TEXT.steps[lang]}>
          {chips.map((chip, index) => (
            <li
              key={`${index}-${chip.name}`}
              title={`${chip.name}(${JSON.stringify(chip.arguments)})`}
              className={cn(
                "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs",
                chip.state === "failed" ? "border-red-300 bg-red-50 text-red-800" : "border-current/15 bg-current/[0.03]",
              )}
            >
              {chip.state === "running" ? (
                <Loader2 className="size-3.5 animate-spin" aria-hidden />
              ) : chip.state === "ok" ? (
                <Check className="size-3.5 text-green-700" aria-hidden />
              ) : (
                <X className="size-3.5" aria-hidden />
              )}
              <Wrench className="size-3 opacity-50" aria-hidden />
              <span>{toolLabel(chip.name, lang)}</span>
              <code className="font-mono text-[11px] opacity-60">{chip.name}</code>
              {chip.state === "failed" && <span className="font-medium">{AGENT_TEXT.failed[lang]}</span>}
              {chip.reused && <span className="opacity-60">· {AGENT_TEXT.reused[lang]}</span>}
              {chip.ms !== null && !chip.reused && <span className="tabular-nums opacity-60">{formatMs(chip.ms)}</span>}
            </li>
          ))}
        </ol>
      )}
      {running && !hasAnswer && !toolsRunning && (
        <p className="flex items-center gap-2 text-sm opacity-70">
          <Loader2 className="size-4 animate-spin" aria-hidden />
          {answered ? AGENT_TEXT.writing[lang] : AGENT_TEXT.thinking[lang]}
        </p>
      )}
    </div>
  );
}

export function formatMs(ms: number): string {
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`;
}
