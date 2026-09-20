import js from "@eslint/js";
import globals from "globals";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import tseslint from "typescript-eslint";

// 项目此前存在 `// eslint-disable-next-line react-hooks/exhaustive-deps`
// 这类注释，但仓库里并没有 ESLint 配置 —— 等于描述了规则却没有执行者。
// 这里补上配置（含 react-hooks 规则），让那些注释真正能被检查。
export default tseslint.config(
  { ignores: ["dist", "node_modules", "src/**/*.test.{ts,tsx}"] },
  {
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    files: ["**/*.{ts,tsx}"],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
    },
    plugins: {
      "react-hooks": reactHooks,
      "react-refresh": reactRefresh,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      "react-refresh/only-export-components": ["warn", { allowConstantExport: true }],
      // 代码里已有多处显式 `// eslint-disable` 并说明理由，这里不再放宽规则；
      // 但把「未使用变量」降为警告，避免历史代码一次性刷屏阻塞接入。
      "@typescript-eslint/no-unused-vars": ["warn", { argsIgnorePattern: "^_" }],
    },
  },
);
