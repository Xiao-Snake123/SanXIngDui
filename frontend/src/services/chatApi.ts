/**
 * 领域问答客户端（讲解助手的数据来源）。
 *
 * 与重构前的关键差别
 * ------------------
 * 旧实现把请求转发给**外部 Dify 服务**，只拿回一段 `answer` 字符串，
 * 出处靠前端 `KNOWLEDGE_BASE` 硬编码 —— 那些条目（「《青铜大立人身份考》，
 * 《考古》2017年第8期」等）无法核实是否真实存在，而且关键词命中时**直接绕过检索**。
 *
 * 现在直接调本项目的 `POST /api/ask`：答案与引用一起返回，引用的出处、原文、
 * 链接全部取自语料条目，**不经过模型**。模型只能决定「用哪一条」。
 *
 * 三条必须由前端如实呈现的信号
 * ----------------------------
 * - `refused=true`：语料里没有可依据的记载。这时 `answer` 是「不能回答」的说明，
 *   而不是一个听起来合理的答案 —— **必须与正常回答区分渲染**，
 *   否则「明确拒绝」会被当成「回答了」。
 * - `caveats`：争议、证据不足、需注意之处。
 * - `citations[].note`：条目的加工说明（如「古籍校勘注已剥离」）。
 *   引用古籍却不说明它被整理过，读者会以为看到的是某个版本的原文。
 */

import { AGENT_ENDPOINTS } from "./agentApi";

export interface AskCitation {
  /** 与答案正文里的 [n] 标记对应 */
  index: number;
  doc_id: string;
  title: string;
  /** 已渲染好的出处文字，如「维基百科编者，青铜纵目面具（抓取于 2026-09-17）」 */
  source: string;
  /** ancient_text / encyclopedia / excavation_report / museum_official … */
  source_type: string;
  /** 由来源类型派生，不是手写值 */
  authority: number;
  license: string;
  /** 可点的原文地址；可能为空（纸质文献只有页码） */
  url: string;
  /** 页码或卷次，如「卷三·二」「上册 第 178 页」 */
  locator: string;
  /** 语料里的原文 */
  quote: string;
  /** 加工说明，可能为空 */
  note: string;
  score: number;
  channels: string[];
}

export interface AskResult {
  question: string;
  answer: string;
  citations: AskCitation[];
  caveats: string[];
  confidence: "high" | "medium" | "low" | string;
  /** 语料里没有可依据的记载，无法回答 */
  refused: boolean;
  evidence_count: number;
  model: string;
  degraded: boolean;
  /** llm = 模型组织过；extractive = 无 Key，直接给原文片段 */
  retrieval_mode: string;
}

const SOURCE_TYPE_LABELS: Record<string, string> = {
  excavation_report: "发掘报告",
  museum_official: "博物馆官方",
  ancient_text: "古籍",
  academic_paper: "学术论文",
  encyclopedia: "百科全书",
  popular_media: "科普媒体",
  project_doc: "项目文档",
  unknown: "来源不明",
};

export function sourceTypeLabel(sourceType: string): string {
  return SOURCE_TYPE_LABELS[sourceType] ?? sourceType;
}

interface AskErrorBody {
  detail?: string | { msg?: string }[];
}

export async function askKnowledgeBase(
  question: string,
  signal?: AbortSignal,
): Promise<AskResult> {
  const response = await fetch(AGENT_ENDPOINTS.ask, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
    signal,
  });

  const rawText = await response.text();
  let data: unknown;

  try {
    data = JSON.parse(rawText);
  } catch {
    throw new Error(
      `问答接口返回了无法解析的响应（HTTP ${response.status}）：${rawText.slice(0, 200)}`,
    );
  }

  if (!response.ok) {
    const body = data as AskErrorBody;
    // FastAPI 的校验错误是数组形式，直接 stringify 会显示成 [object Object]
    const detail =
      typeof body.detail === "string"
        ? body.detail
        : Array.isArray(body.detail)
          ? body.detail.map((item) => item?.msg ?? "").filter(Boolean).join("；")
          : "";
    throw new Error(detail || `问答失败（HTTP ${response.status}）`);
  }

  const result = data as AskResult;
  if (!result.answer) {
    throw new Error("问答接口未返回有效回答");
  }
  return result;
}
