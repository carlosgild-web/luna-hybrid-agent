/** Agent UI strings in both portal languages; the console uses "en". */
export type AgentLang = "en" | "es";

type Bi = Record<AgentLang, string>;

const TOOLS: Record<string, Bi> = {
  search_documents: { en: "Searching the documents", es: "Buscando en los documentos" },
  check_inventory: { en: "Checking the inventory", es: "Revisando el inventario" },
  sales_report: { en: "Reading the sales records", es: "Leyendo las ventas" },
  supplier_purchases: { en: "Reading the purchases", es: "Leyendo las compras" },
  sql_select: { en: "Querying the database", es: "Consultando la base de datos" },
  fx_rate: { en: "Getting the exchange rate", es: "Obteniendo el tipo de cambio" },
  make_chart: { en: "Drawing a chart", es: "Haciendo una gráfica" },
};

export const AGENT_TEXT = {
  thinking: { en: "Luna is deciding what to look up…", es: "Luna está decidiendo qué consultar…" },
  writing: { en: "Writing the answer…", es: "Escribiendo la respuesta…" },
  failed: { en: "failed", es: "falló" },
  reused: { en: "same call, earlier result", es: "misma consulta, resultado anterior" },
  steps: { en: "Steps", es: "Pasos" },
  inspect: { en: "Inspect the run", es: "Ver el recorrido" },
  showData: { en: "Show the data", es: "Ver los datos" },
  hideData: { en: "Hide the data", es: "Ocultar los datos" },
  chartFrom: { en: "Values from the tool results", es: "Valores de los resultados de las herramientas" },
  chartUnsupported: {
    en: "Some values in this chart are not in any tool result",
    es: "Algunos valores de esta gráfica no están en ningún resultado",
  },
  stoppedAgent: {
    en: "Stopped watching. Luna finishes the run in the background and saves it to this conversation.",
    es: "Dejaste de ver el progreso. Luna termina en segundo plano y lo guarda en esta conversación.",
  },
  mode: { en: "Answer with", es: "Responder con" },
  modeDocs: { en: "Documents", es: "Documentos" },
  modeAgent: { en: "Documents + business data", es: "Documentos + datos del negocio" },
} satisfies Record<string, Bi>;

export function toolLabel(name: string, lang: AgentLang): string {
  return TOOLS[name]?.[lang] ?? name;
}

export function formatNumber(value: unknown, lang: AgentLang): string {
  return typeof value === "number"
    ? new Intl.NumberFormat(lang === "es" ? "es-MX" : "en-US", { maximumFractionDigits: 2 }).format(value)
    : String(value ?? "");
}
