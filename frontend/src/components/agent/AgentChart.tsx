import { useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { ChartSpec } from "@/api/types";
import { AGENT_TEXT, type AgentLang, formatNumber } from "./labels";

/**
 * A chart `make_chart` stored (decision 0038), drawn with recharts. Loaded lazily
 * (AgentCharts.tsx) so recharts stays out of the main bundle.
 *
 * Colors: the dataviz reference categorical palette, in fixed order, validated on
 * the white surface (adjacent CVD ΔE ≥ 9.1). Three slots sit under 3:1 contrast,
 * so every chart has a data table one click away and a legend from two series up.
 */
const SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"];
const INK = "#52514e";
const GRID = "#e4e4e7";

export default function AgentChart({ spec, lang = "en" }: { spec: ChartSpec; lang?: AgentLang }) {
  const [table, setTable] = useState(false);
  const format = (value: unknown) => formatNumber(value, lang);
  const legend = spec.series.length >= 2;
  const axis = { stroke: GRID, tick: { fill: INK, fontSize: 12 }, tickLine: false } as const;

  return (
    <figure className="mt-3 rounded-lg border border-current/10 bg-white p-3 text-[#0b0b0b]">
      <figcaption className="mb-2 flex items-baseline justify-between gap-3">
        <span className="text-sm font-semibold">{spec.title}</span>
        <button
          type="button"
          onClick={() => setTable((t) => !t)}
          className="shrink-0 text-xs text-[#52514e] underline-offset-2 hover:underline"
        >
          {table ? AGENT_TEXT.hideData[lang] : AGENT_TEXT.showData[lang]}
        </button>
      </figcaption>
      <div className="h-64 w-full">
        <ResponsiveContainer width="100%" height="100%">
          {spec.kind === "pie" ? (
            <PieChart>
              <Pie
                data={spec.data}
                dataKey={spec.series[0]}
                nameKey="label"
                innerRadius="45%"
                outerRadius="80%"
                paddingAngle={1}
                stroke="#ffffff"
                strokeWidth={2}
                label={({ name }) => String(name)}
                isAnimationActive={false}
              >
                {spec.data.map((row, i) => (
                  <Cell key={row.label} fill={SERIES[i % SERIES.length]} />
                ))}
              </Pie>
              <Tooltip formatter={(v) => format(v)} />
              <Legend />
            </PieChart>
          ) : spec.kind === "bar" ? (
            <BarChart data={spec.data} margin={{ top: 4, right: 8, bottom: 0, left: 8 }} barGap={2}>
              <CartesianGrid vertical={false} stroke={GRID} />
              <XAxis dataKey="label" {...axis} />
              <YAxis {...axis} axisLine={false} tickFormatter={format} width={72} label={yLabel(spec)} />
              <Tooltip formatter={(v) => format(v)} cursor={{ fill: "rgba(0,0,0,0.04)" }} />
              {legend && <Legend />}
              {spec.series.map((name, i) => (
                <Bar key={name} dataKey={name} fill={SERIES[i]} radius={[4, 4, 0, 0]} maxBarSize={48} isAnimationActive={false} />
              ))}
            </BarChart>
          ) : (
            <LineChart data={spec.data} margin={{ top: 4, right: 16, bottom: 0, left: 8 }}>
              <CartesianGrid vertical={false} stroke={GRID} />
              <XAxis dataKey="label" {...axis} />
              <YAxis {...axis} axisLine={false} tickFormatter={format} width={72} label={yLabel(spec)} />
              <Tooltip formatter={(v) => format(v)} />
              {legend && <Legend />}
              {spec.series.map((name, i) => (
                <Line
                  key={name}
                  type="monotone"
                  dataKey={name}
                  stroke={SERIES[i]}
                  strokeWidth={2}
                  dot={{ r: 4, strokeWidth: 2, stroke: "#ffffff", fill: SERIES[i] }}
                  activeDot={{ r: 5 }}
                  isAnimationActive={false}
                />
              ))}
            </LineChart>
          )}
        </ResponsiveContainer>
      </div>
      {table && (
        <table className="mt-3 w-full text-xs">
          <thead>
            <tr className="border-b text-left text-[#52514e]">
              <th className="py-1 pr-3 font-medium" />
              {spec.series.map((name) => (
                <th key={name} className="py-1 pr-3 text-right font-medium">
                  {name}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {spec.data.map((row) => (
              <tr key={row.label} className="border-b last:border-0">
                <td className="py-1 pr-3">{row.label}</td>
                {spec.series.map((name) => (
                  <td key={name} className="py-1 pr-3 text-right tabular-nums">
                    {format(row[name])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </figure>
  );
}

function yLabel(spec: ChartSpec) {
  return spec.y_label
    ? { value: spec.y_label, angle: -90, position: "insideLeft" as const, fill: INK, fontSize: 12, dx: -4 }
    : undefined;
}
