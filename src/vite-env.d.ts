/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_CHAT_APP_API_KEY?: string;
  readonly VITE_DASHSCOPE_API_KEY?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
