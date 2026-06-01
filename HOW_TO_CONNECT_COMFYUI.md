# 网站如何对接 ComfyUI

本文档说明当前项目是如何与 ComfyUI 对接的，也可以作为你后续接入其他工作流时的参考模板。

## 1. 当前架构

当前项目采用的是：

- 前端页面负责收集用户参数
- 前端服务层负责读取并改写工作流
- Vite 代理负责把浏览器请求转发给 ComfyUI
- ComfyUI 负责执行工作流并生成图片

也就是说，现在不是“前端 -> 自建后端 -> ComfyUI”，而是：

`前端页面 -> Vite 代理 -> ComfyUI`

---

## 2. 关键文件位置

### 页面入口
- `src/app/pages/ai/AIPage.tsx`

作用：
- 接收用户选择的身份、场景、器物、风格
- 点击按钮后调用 `generateSceneImage()`
- 接收生成进度和结果图片并展示在页面上

### ComfyUI 对接核心
- `src/services/aiApi.ts`

作用：
- 读取工作流模板
- 动态替换 prompt、负向词、seed、输出文件名前缀
- 把 ComfyUI 界面工作流格式转换成 API 格式
- 提交到 ComfyUI
- 轮询生成结果
- 返回最终图片地址

### 代理配置
- `vite.config.ts`

作用：
- 把前端访问的 `/comfy/...` 请求转发到 `http://127.0.0.1:8188/...`
- 移除 `origin` 和 `referer` 请求头，避免 ComfyUI 返回 403

### 工作流模板
- `public/workflows/sanxingdui_lora.json`

作用：
- 提供前端读取的基础工作流
- 前端会在运行时修改其中部分节点参数

---

## 3. 整个对接流程

### 第一步：用户在页面选择参数
用户在 AI 页面里选择：
- 人物身份
- 场景
- 器物
- 风格

这些数据在页面组件中被保存，然后交给 `generateSceneImage()`。

### 第二步：前端读取工作流模板
`src/services/aiApi.ts` 中的 `fetchWorkflowTemplate()` 会请求：

`/workflows/sanxingdui_lora.json`

因为这个文件在 `public` 目录下，所以浏览器可以直接访问。

### 第三步：动态替换工作流参数
`applySceneParams()` 会修改工作流中的关键节点：

- 正向提示词节点
- 负向提示词节点
- KSampler 的 seed
- SaveImage 的输出文件前缀

当前项目里，典型修改如下：

- `node.id === 9`：写入正向 prompt
- `node.id === 10`：写入负向 prompt
- `node.id === 6`：写入 seed
- `node.id === 8`：写入输出文件名前缀

这一步的本质就是：

“把用户在网页中选的参数，塞进 ComfyUI 工作流里”。

### 第四步：把 UI 工作流格式转换成 API 格式
ComfyUI 保存出来的工作流 JSON，和 `/prompt` 接口真正需要的 JSON 结构不是一回事。

所以 `convertWorkflowToApi()` 会做一次转换。

转换的核心包括：
- 解析 `links`
- 把节点之间的连接关系映射成 API 输入引用
- 把 `widgets_values` 按顺序映射到对应输入字段
- 生成 ComfyUI `/prompt` 接口需要的 `prompt` 对象

这里最关键的一张表就是：

`WIDGET_ORDER`

它定义了每种节点类型的参数顺序，例如：
- `KSampler`
- `LoraLoader`
- `CheckpointLoaderSimple`
- `CLIPTextEncode`
- `SaveImage`

如果以后你换工作流，最容易出问题的地方就是这里。

### 第五步：提交到 ComfyUI
转换完成后，前端调用：

`POST /comfy/prompt`

注意：这里不是直接请求 `8188`，而是请求前端本地路径 `/comfy/prompt`。

原因是浏览器直接请求 ComfyUI 可能会被跨域或来源校验拦截。

Vite 会把这个请求转发到：

`http://127.0.0.1:8188/prompt`

### 第六步：轮询任务状态
ComfyUI 返回 `prompt_id` 后，前端会循环请求：

`/comfy/history/{prompt_id}`

如果任务完成，就从返回结果里取出图片输出信息。

### 第七步：拼接图片地址并显示
当拿到输出图片的文件名后，前端会调用：

`/comfy/view?filename=...&subfolder=...&type=output`

然后把这个地址作为页面上的图片 URL 显示出来。

---

## 4. 当前项目中最重要的函数

以下函数都在 `src/services/aiApi.ts`：

### `createPrompt(params)`
作用：
- 根据页面选择的内容，拼出正向提示词

### `fetchWorkflowTemplate()`
作用：
- 从 `public/workflows` 读取工作流模板

### `applySceneParams(workflow, params)`
作用：
- 把网页参数写进工作流节点里

### `convertWorkflowToApi(workflow)`
作用：
- 把 ComfyUI 前端工作流 JSON 转成 `/prompt` API 格式

### `generateSceneImage(params, onProgress)`
作用：
- 串起整条流程
- 加载模板
- 改工作流
- 提交任务
- 轮询结果
- 返回图片 URL

---

## 5. Vite 代理为什么是必须的

当前代理配置在 `vite.config.ts`。

### 为什么不能直接在浏览器里请求 `http://127.0.0.1:8188`？
因为浏览器会自动带上：
- `Origin`
- `Referer`

而 ComfyUI 在某些情况下会拒绝这些来源，直接返回 403。

### 当前项目怎么解决的？
通过 Vite 代理：
- 前端请求 `/comfy/...`
- Vite 转发到 `127.0.0.1:8188`
- 转发时移除 `origin` 和 `referer`

这样对 ComfyUI 来说，请求更像是本机脚本调用，而不是浏览器跨域请求。

---

## 6. 如果你要替换成自己的工作流，怎么做

### 方法一：替换工作流文件
把新的 ComfyUI 工作流导出 JSON 后，替换：

`public/workflows/sanxingdui_lora.json`

### 方法二：检查节点 ID 是否还一致
如果你的新工作流里：
- prompt 节点不是 9
- negative 节点不是 10
- sampler 节点不是 6
- save 节点不是 8

那就必须改 `applySceneParams()`。

### 方法三：检查节点类型参数顺序
如果新工作流里用了新节点或不同节点，可能要更新：

`WIDGET_ORDER`

否则转换成 API 格式时就会错位，导致 400 错误。

---

## 7. 最常见的报错与处理方法

### 报错 1：提交到 ComfyUI 失败: 403
原因通常是：
- Vite 代理没生效
- 改了代理后没重启 `npm run dev`

处理方法：
1. 停掉前端 dev 服务
2. 重新执行 `npm run dev`
3. 浏览器强制刷新

### 报错 2：提交到 ComfyUI 失败: 400
原因通常是：
- 工作流转换后的 JSON 不符合 ComfyUI `/prompt` 接口要求
- 节点参数顺序不对
- 某些节点缺少输入字段

重点检查：
- `WIDGET_ORDER`
- `convertWorkflowToApi()`
- 工作流节点 ID 和类型

### 报错 3：页面一直显示生成中
原因通常是：
- ComfyUI 没有真的执行成功
- `/history/{prompt_id}` 一直没有产出图片

检查：
- ComfyUI 是否在线
- ComfyUI 控制台有没有报错
- 输出目录里有没有图片生成

---

## 8. 小白接入 ComfyUI 的最短步骤

### 步骤 1：准备工作流
在 ComfyUI 里把工作流跑通，并导出 JSON。

### 步骤 2：把 JSON 放到前端可访问目录
放到：

`public/workflows/你的工作流.json`

### 步骤 3：写一个读取工作流的函数
在前端用 `fetch('/workflows/你的工作流.json')` 读取。

### 步骤 4：找到你要动态修改的节点
例如：
- prompt 节点
- negative 节点
- seed 节点
- 保存节点

### 步骤 5：把工作流转换成 ComfyUI API 格式
写或复用 `convertWorkflowToApi()`。

### 步骤 6：通过 `/comfy/prompt` 提交
不要直接在浏览器里硬连 8188，尽量通过 Vite 代理。

### 步骤 7：轮询 `/comfy/history/{prompt_id}`
拿到结果后再用 `/comfy/view` 显示图片。

---

## 9. 当前项目一句话总结

当前项目是：

“前端读取 ComfyUI 工作流模板 -> 动态替换节点参数 -> 转成 API prompt -> 通过 Vite 代理提交给 ComfyUI -> 轮询历史记录 -> 返回图片并显示”。

---

## 10. 建议你重点掌握的 3 个文件

如果你只想真正看懂这个对接，优先看这 3 个文件：

1. `src/services/aiApi.ts`
2. `vite.config.ts`
3. `public/workflows/sanxingdui_lora.json`

看懂这 3 个文件，你基本就看懂整个 ComfyUI 对接逻辑了。
