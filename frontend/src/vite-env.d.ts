/// <reference types="vite/client" />

/**
 * 前端可见的环境变量。
 *
 * 这里只应出现**可以公开**的值。任何 API Key 都不要以 `VITE_` 前缀声明 ——
 * Vite 会把 `VITE_*` 原样打进浏览器产物，等于把密钥公开发布。
 *
 * 这里曾声明过 `VITE_CHAT_APP_API_KEY` 与 `VITE_DASHSCOPE_API_KEY`，两者均已移除：
 * 前者对应的外部对话通道已被本项目的 `/api/ask` 取代；
 * 后者从未被任何代码使用，留着只会诱使人误填 —— 一旦填了就会被打包出去。
 * 模型调用全部发生在后端（`backend/.env` 的 `DASHSCOPE_API_KEY`），前端不接触任何密钥。
 */
interface ImportMetaEnv {
  /** 覆盖多智能体后端地址；留空时走 vite 代理的 `/agent` 前缀 */
  readonly VITE_AGENT_BASE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
