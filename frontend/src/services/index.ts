/**
 * 服务层统一出口。
 *
 * - `agentApi`：多智能体复原服务（SSE 流式 + 元信息接口），AI 复原页面的唯一数据来源；
 * - `chatApi`：悬浮讲解助手（保留原有对话能力）。
 *
 * 注意：此前的 `aiApi.ts`（单次文生图调用）已在重构中移除 ——
 * 它只做一次 prompt 拼接 + fetch，没有检索、规划与质检，
 * 正是「Demo 感」的来源。相关能力现由 `agentApi` 的流式接口承担。
 */
export * from './agentApi'
export * from './chatApi'
