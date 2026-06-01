# 三星堆项目：前端直连 ComfyUI 对接指南（小白版）

## 一、这套项目现在是怎么对接 ComfyUI 的？

当前项目已经是“纯前端 + ComfyUI”模式，不再依赖后端 API。

核心对接点有 4 个：

1. 页面触发入口：src/app/pages/ai/AIPage.tsx
2. ComfyUI 请求逻辑：src/services/aiApi.ts
3. 代理配置（解决跨域/403）：vite.config.ts
4. 工作流模板：public/workflows/sanxingdui_lora.json

---

## 二、完整请求链路（从点按钮到出图）

1. 用户在 AI 页面选择身份、场景、器物、风格。
2. 点击生成后，AIPage 调用 `generateSceneImage()`。
3. `aiApi.ts` 读取工作流模板 `public/workflows/sanxingdui_lora.json`。
4. `applySceneParams()` 替换：
   - 正向提示词
   - 负向提示词
   - KSampler 的 seed
   - SaveImage 的文件名前缀
5. `convertWorkflowToApi()` 把 UI 工作流格式转换成 ComfyUI API 所需的 `prompt` 结构。
6. 前端调用 `/comfy/prompt` 提交任务（由 Vite 代理转发到 `http://127.0.0.1:8188/prompt`）。
7. 轮询 `/comfy/history/{prompt_id}`。
8. 拿到输出图片后，拼接 `/comfy/view?...` 地址并展示。

---

## 三、为什么要用 `/comfy` 而不是直接调 8188？

浏览器直接请求 ComfyUI 容易遇到跨域和来源校验问题（典型是 403）。

所以项目使用了 Vite 代理：

- 前端只访问同源路径 `/comfy/...`
- Vite 再转发到 `127.0.0.1:8188`
- 并移除 `origin/referer` 请求头，避免 ComfyUI 拒绝请求

这部分配置在 `vite.config.ts`。

---

## 四、最简启动步骤

### 1）先启动 ComfyUI
确保本机 `http://127.0.0.1:8188` 可访问。

### 2）启动前端
在项目根目录执行：

```bash
npm run dev
```

### 3）打开页面
访问终端里显示的地址（通常是 `http://localhost:5173`）。

### 4）开始生成
进入 AI 页面后点击“开始复原”。

---

## 五、常见修改位置（开发最常用）

### 1）改 Prompt 拼接规则
修改 `src/services/aiApi.ts` 里的 `createPrompt()`。

### 2）改默认负向词
修改 `src/services/aiApi.ts` 里的 `DEFAULT_NEGATIVE_PROMPT`。

### 3）改采样参数、模型、LoRA、节点连线
修改 `public/workflows/sanxingdui_lora.json`。

### 4）改哪些节点被动态替换
修改 `src/services/aiApi.ts` 里的 `applySceneParams()`（按节点 id）。

---

## 六、报错排查速查

### 报错：提交到 ComfyUI 失败: 403
可能原因：
- 代理未生效
- 改了 `vite.config.ts` 后没重启 dev 服务

处理：
1. 先停止前端
2. 重新执行 `npm run dev`
3. 浏览器强刷（Ctrl+F5）

### 报错：400
可能原因：
- 工作流转换后字段不匹配 ComfyUI 节点输入

检查：
- `src/services/aiApi.ts` 的 `WIDGET_ORDER`
- `convertWorkflowToApi()` 映射逻辑
- `public/workflows/sanxingdui_lora.json` 节点结构

### 页面一直转圈不出图
检查：
- ComfyUI 是否在线（8188）
- 工作流文件路径是否存在
- ComfyUI 是否有实际输出（output 目录）

---

## 七、给小白的“一句话记忆”

点生成后，前端读取工作流、替换参数、转换成 ComfyUI API 格式，提交到 `/comfy/prompt`，轮询 `/comfy/history`，最后用 `/comfy/view` 展示图片。

---

## 八、当前项目关键文件清单

- src/app/pages/ai/AIPage.tsx
- src/services/aiApi.ts
- vite.config.ts
- public/workflows/sanxingdui_lora.json

以上 4 个文件就是当前对接主链路。
