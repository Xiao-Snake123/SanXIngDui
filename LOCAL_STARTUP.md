# 🚀 本地开发快速启动

## 🤖 RAG 智能助手（Open WebUI 版）

当前前端已支持：
- 命中本地 `KNOWLEDGE_BASE` 的固定问答时，直接返回内置答案
- 未命中时，自动调用本机 Open WebUI 的模型回答

### 一次性配置

在项目根目录创建 `.env.local`：

```bash
cp .env.example .env.local
```

然后填写：

```env
VITE_OPENWEBUI_API_KEY=你的_Open_WebUI_API_Token
VITE_OPENWEBUI_MODEL=111
```

### 运行要求

- Open WebUI 本机可访问：`http://127.0.0.1:8080`
- 对应模型已在 Open WebUI / Ollama 中可正常对话
- 前端通过 Vite 代理 `/openwebui` 转发到本机 Open WebUI，避免浏览器跨域

### 启动步骤

```bash
npm run dev
```

打开：`http://localhost:5173`

可直接在右下角「古蜀智脑 / RAG 智能助手」里测试：
- `金杖的图案` → 命中本地知识库
- `三星堆为什么没有成熟文字系统？` → 走 Open WebUI 模型

### 常见问题

**1. 提示未配置 API Key**

说明 `.env.local` 还没有填写 `VITE_OPENWEBUI_API_KEY`，填写后重启前端即可。

**2. 提示无法连接 Open WebUI**

先检查：

```bash
curl http://127.0.0.1:3000
```

如果无法访问，先启动 Open WebUI。

**3. 浏览器里出现 404 / 代理失败**

确认你是用 `npm run dev` 启动前端；`/openwebui` 代理在 Vite 开发环境下生效。

## ⚡ 3 秒启动命令

### 终端 1 - 启动后端
```bash
cd backend
python main.py
```

### 终端 2 - 启动前端
```bash
npm run dev
```

完成！现在可以访问 http://localhost:5173 了。

---

## 📋 完整配置检查

### ✅ 前置条件确认
- [x] ComfyUI 运行在 http://localhost:8188/
- [x] 工作流文件存在：`backend/workflows/sanxingdui_lora.json`
- [x] 模型文件在 ComfyUI 包下
- [x] 自定义节点已安装

### ✅ 验证步骤

**1. 检查 ComfyUI 连接**
```bash
curl http://localhost:8188/system_stats
```
应该返回 JSON 格式的系统信息

**2. 检查后端启动**
```bash
curl http://localhost:8000/health
```
应该返回：
```json
{
  "status": "ok",
  "service": "Sanxingdui AI Restoration Engine",
  "version": "1.0.0",
  "comfyui_server": "http://localhost:8188",
  "output_dir": "..."
}
```

**3. 检查 ComfyUI 连接状态**
```bash
curl http://localhost:8000/api/comfyui/check
```
应该返回：
```json
{"status":"connected","server":"http://localhost:8188"}
```

---

## 🎯 首次生成测试

### 测试 API

**步骤 1：提交生成任务**
```bash
curl -X POST http://localhost:8000/api/ai-restoration/generate \
  -H "Content-Type: application/json" \
  -d '{
    "identity": "大祭司",
    "scene": "青铜神树祭坛",
    "item": "金杖",
    "style": "史诗油画风"
  }'
```

返回示例：
```json
{
  "prompt_id": "abc123def456...",
  "status": "submitted",
  "message": "✅ 图像生成已提交，请查询结果"
}
```

复制返回的 `prompt_id`

**步骤 2：查询生成结果**
```bash
# 替换 PROMPT_ID 为实际 ID
curl http://localhost:8000/api/ai-restoration/result/PROMPT_ID
```

期望流程：
- 前几次返回：`"status": "processing"`
- 最终返回：`"status": "completed"` 和 `"image_url": "/api/images/..."`

**步骤 3：访问生成的图片**
```
http://localhost:8000/api/images/filename.png
```

---

## 📁 文件结构概览

```
backend/
├── main.py                          # 核心应用（已优化）
├── requirements.txt                 # Python 依赖
├── .env                             # ✨ 已更新：http://localhost:8188
├── workflows/
│   └── sanxingdui_lora.json        # ComfyUI 工作流
└── outputs/                         # ✨ 生成的图片保存位置
```

---

## 🔍 实时监听日志

**后端日志示例：**
```
🚀 启动三星堆 AI 复原引擎（本地开发版）
📡 API 服务器: http://0.0.0.0:8000
📡 ComfyUI 服务器: http://localhost:8188
📁 输出目录: /path/to/backend/outputs
🖼️  图片访问前缀: http://localhost:8000/api/images
📚 API 文档: http://localhost:8000/docs
```

**生成过程日志示例：**
```
📝 收到生成请求: 大祭司 / 青铜神树祭坛 / 金杖 / 史诗油画风
🎨 Generated Prompt: ancient Sanxingdui high priest...
✓ 已更新 Prompt 节点
✅ 工作流已提交: abc123def456
⏳ 等待中... (10s)
⏳ 等待中... (20s)
⏳ 等待中... (30s)
✅ 获取到结果: abc123def456
✅ 生成的图片 URL: /api/images/sanxingdui_123.png
```

---

## 🎨 前端 UI 集成

在 `src/app/components/AIRestoration.tsx` 中：

```typescript
import { generateImage } from '@/services/aiApi'

const handleGenerate = async () => {
  setLoading(true)
  try {
    const imageUrl = await generateImage(
      {
        identity,
        scene,
        item,
        style,
        seed: randomSeed
      },
      (status, elapsed) => {
        setStatus(`${status} (已用时 ${(elapsed / 1000).toFixed(0)}s)`)
      }
    )
    
    setGeneratedImage(imageUrl)
    setStatus('生成完成！')
  } catch (error) {
    setStatus(`错误: ${error.message}`)
  } finally {
    setLoading(false)
  }
}
```

---

## 🐛 常见问题排查

### Q1：后端启动失败 - ModuleNotFoundError
```
ModuleNotFoundError: No module named 'fastapi'
```
**A：** 安装依赖
```bash
cd backend
pip install -r requirements.txt
```

### Q2：无法连接到 ComfyUI
```
Error: 与 ComfyUI 通信失败
```
**A：** 
- 检查 ComfyUI 是否运行：`curl http://localhost:8188/system_stats`
- 检查 `.env` 中的 `COMFYUI_SERVER=http://localhost:8188`
- 尝试重启 ComfyUI

### Q3：工作流文件不存在
```
Error: 工作流文件不存在
```
**A：**
- 确保 `backend/workflows/sanxingdui_lora.json` 存在
- 检查文件路径和权限

### Q4：生成超时
```
Error: 生成超时
```
**A：**
- 增加 `.env` 中的 `MAX_GENERATION_TIME` (默认 600 秒)
- 检查 GPU 显存是否充足
- 检查 ComfyUI 是否在正常处理

### Q5：前端无法调用后端
```
Error: fetch failed / CORS error
```
**A：**
- 确保后端运行在 `http://localhost:8000`
- 检查 `src/services/aiApi.ts` 中的 `API_BASE`
- 后端已配置 CORS（允许所有来源）

---

## 📊 生成时间参考

### 首次生成（模型加载）
- 模型加载：15-30 秒
- 采样生成：30-60 秒
- 结果保存：5 秒
- **总计：50-95 秒**

### 后续生成（模型缓存）
- 采样生成：30-60 秒
- 结果保存：5 秒
- **总计：35-65 秒**

---

## 💾 生成结果存储

所有生成的图片自动保存在：
```
backend/outputs/
```

访问方式：
```
http://localhost:8000/api/images/filename.png
```

---

## 🛠️ 开发技巧

### 实时查看 API 文档
访问：http://localhost:8000/docs

### 获取当前配置
```bash
curl http://localhost:8000/api/config
```

### 重启后端（开发模式自动重载）
```bash
# 按 Ctrl+C 停止
# 修改代码后自动重新加载
```

### 查看输出目录
```bash
ls -lh backend/outputs/
```

---

## ✨ 已优化的本地配置

- ✅ **ComfyUI 地址**：http://localhost:8188
- ✅ **API 服务器**：http://localhost:8000
- ✅ **前端应用**：http://localhost:5173
- ✅ **输出目录**：backend/outputs (本地文件夹)
- ✅ **静态文件服务**：/api/images (直接返回图片 URL)
- ✅ **CORS 配置**：允许本地所有端口
- ✅ **日志记录**：详细的操作日志

---

## 🎉 您现在可以开始了！

1. 启动后端：`cd backend && python main.py`
2. 启动前端：`npm run dev`
3. 打开浏览器：http://localhost:5173
4. 开始生成三星堆文物！

有任何问题，查看日志或访问 http://localhost:8000/docs

---

**Happy Coding! 🚀**
