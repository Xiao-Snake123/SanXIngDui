# ✅ 三星堆 AI 复原引擎 - 启动清单

## 📋 前置检查

### ComfyUI 环境
- [ ] ComfyUI 已安装并运行在 http://127.0.0.1:8188
- [ ] SDXL 基础模型已下载: `sd_xl_base_1.0.safetensors`
- [ ] LoRA 微调模型已下载: `sdxl_liren_webui.safetensors` (或其他文物微调模型)
- [ ] 自定义节点已安装: `SXD_ZH2ENPrompt` (中文转英文)
- [ ] ComfyUI 能正常运行工作流

验证命令:
```bash
curl http://127.0.0.1:8188/system_stats
```

### Python 环境
- [ ] Python 3.8+ 已安装
- [ ] pip 包管理器可用

验证命令:
```bash
python --version
pip --version
```

### Node.js 环境
- [ ] Node.js 16+ 已安装
- [ ] npm 包管理器可用

验证命令:
```bash
node --version
npm --version
```

## 🚀 后端启动步骤

### 1️⃣ 进入后端目录
```bash
cd backend
```

### 2️⃣ 安装 Python 依赖
```bash
pip install -r requirements.txt
```

检查安装结果:
- [ ] fastapi 已安装
- [ ] uvicorn 已安装
- [ ] requests 已安装
- [ ] python-dotenv 已安装

### 3️⃣ 配置环境变量
编辑 `.env` 文件，确保:
- [ ] `COMFYUI_SERVER=http://127.0.0.1:8188` (根据实际情况修改)
- [ ] `API_PORT=8000` (确保端口不被占用)
- [ ] `DEBUG=True` (开发模式)

### 4️⃣ 启动后端服务
```bash
python main.py
```

期望输出:
```
🚀 启动服务器: 0.0.0.0:8000
📡 ComfyUI 服务器: http://127.0.0.1:8188
INFO:     Uvicorn running on http://0.0.0.0:8000
```

- [ ] 后端服务成功启动
- [ ] 没有 ImportError 或 ConnectionError

### 5️⃣ 验证后端健康状态
在新终端运行:
```bash
curl http://localhost:8000/health
```

期望返回:
```json
{"status":"ok","service":"Sanxingdui AI Restoration Engine"}
```

- [ ] 健康检查返回成功

### 6️⃣ 验证 ComfyUI 连接
```bash
curl http://localhost:8000/api/comfyui/check
```

期望返回:
```json
{"status":"connected","server":"http://127.0.0.1:8188"}
```

- [ ] ComfyUI 连接成功

## 🎨 前端启动步骤

### 1️⃣ 返回项目根目录
```bash
cd ..
```

### 2️⃣ 启动前端开发服务器
```bash
npm run dev
```

期望输出:
```
  ➜  Local:   http://localhost:5173/
```

- [ ] 前端服务成功启动
- [ ] 编译没有错误

### 3️⃣ 访问应用
打开浏览器访问: http://localhost:5173

- [ ] 页面正常加载
- [ ] 没有控制台错误

## 🧪 功能测试

### 1️⃣ API 测试 - 提交生成

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

- [ ] 返回成功响应，包含 `prompt_id`
- [ ] 状态为 `submitted`

### 2️⃣ API 测试 - 查询结果

将上面返回的 `prompt_id` 替换为 PROMPT_ID:

```bash
curl http://localhost:8000/api/ai-restoration/result/PROMPT_ID
```

期望流程:
1. 前几次查询返回 `"status": "processing"`
2. 生成完成后返回 `"status": "completed"` 和 `image_url`

- [ ] 最终获取到 `image_url`

### 3️⃣ 前端 UI 测试

访问 http://localhost:5173/ai 页面:

- [ ] 能看到身份、场景、物品、风格的选择项
- [ ] 点击"生成复原图"按钮
- [ ] 页面显示"生成中..."的加载状态
- [ ] 一段时间后显示生成的图像
- [ ] 能够下载或分享生成的图像

## 🔍 调试常见问题

### 问题 1：后端启动失败
```
ModuleNotFoundError: No module named 'fastapi'
```
**解决:** 
```bash
pip install -r requirements.txt --upgrade
```

### 问题 2：无法连接 ComfyUI
```
Error: 与 ComfyUI 通信失败
```
**检查:**
- [ ] ComfyUI 进程是否运行中
- [ ] `.env` 中的 COMFYUI_SERVER 地址是否正确
- [ ] 防火墙是否阻止 8188 端口
- [ ] 运行 `curl http://127.0.0.1:8188/system_stats`

### 问题 3：工作流文件不存在
```
Error: 工作流文件不存在
```
**检查:**
- [ ] 确保 `backend/workflows/sanxingdui_lora.json` 存在
- [ ] 检查文件权限和编码格式

### 问题 4：模型文件缺失
```
Error: Model not found: sd_xl_base_1.0.safetensors
```
**解决:**
在 ComfyUI 中下载所需模型或检查模型路径

### 问题 5：端口被占用
```
Error: Address already in use
```
**解决:**
更改 `.env` 中的 `API_PORT` 为其他未占用端口

### 问题 6：前端请求超时
```
Error: 请求超时
```
**解决:**
- [ ] 确保后端服务运行中
- [ ] 检查 `src/services/aiApi.ts` 中的 `API_BASE` 地址
- [ ] 增加 `.env` 中的 `MAX_GENERATION_TIME`

## 📊 性能验证

### GPU 显存检查
运行 ComfyUI 时，监听 GPU 显存占用:

```bash
nvidia-smi --query-gpu=memory.used,memory.total --format=csv -l 1
```

应该看到:
- [ ] 模型加载时显存增加
- [ ] 采样时显存占用稳定
- [ ] 完成后显存释放

### 生成时间统计

第一次生成:
- [ ] 预计耗时 90-120 秒（包括模型加载）

后续生成:
- [ ] 预计耗时 60-90 秒（模型已在显存）

## 🎯 成功标志

✅ 以下所有项均打勾表示部署成功:

- [ ] 后端服务正常启动
- [ ] 前端服务正常启动
- [ ] 健康检查通过
- [ ] ComfyUI 连接成功
- [ ] API 能成功生成图像
- [ ] 前端 UI 能显示生成结果
- [ ] 生成时间在可接受范围内

## 📚 有用的命令速查表

```bash
# 查看后端日志
tail -f backend/backend.log

# 查看 ComfyUI 日志
tail -f /path/to/comfyui/logs/

# 停止后端服务
pkill -f "python main.py"

# 检查端口占用
netstat -tlnp | grep 8000

# 查看 API 文档
open http://localhost:8000/docs

# 重启前端
npm run dev
```

## 📞 需要帮助？

检查以下文档:
1. [快速开始指南](./backend/QUICKSTART.md)
2. [项目集成说明](./backend/INTEGRATION.md)
3. [API 文档](http://localhost:8000/docs)

---

**祝您部署成功！** 🎉
