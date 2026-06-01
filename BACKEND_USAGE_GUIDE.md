# 三星堆 AI 文物复原 - 后端完整使用指南

## 🚀 快速开始

### 前置条件
1. ✅ ComfyUI 已安装并运行在 http://localhost:8188
2. ✅ SDXL 1.0 模型已加载
3. ✅ LoRA 模型已加载 (sdxl_liren_webui)
4. ✅ Python 3.8+

### 启动后端

**方法 1: 使用启动脚本（推荐）**
```bash
cd /home/ai/下载/SanxingduiRuinsPro
bash start.sh
```

**方法 2: 直接运行**
```bash
cd /home/ai/下载/SanxingduiRuinsPro/backend
python fastapi_backend.py
```

**方法 3: 使用 Uvicorn**
```bash
cd /home/ai/下载/SanxingduiRuinsPro/backend
pip install fastapi uvicorn requests
uvicorn fastapi_backend:app --host 0.0.0.0 --port 8000 --reload
```

---

## 📋 API 文档

### 1. 生成图像

**端点**: `POST /api/ai-restoration/generate`

**请求体**:
```json
{
  "identity": "大祭司",
  "scene": "青铜神树祭坛",
  "item": "金杖",
  "style": "史诗油画风",
  "custom_prompt": null,
  "seed": null
}
```

**响应**:
```json
{
  "success": true,
  "task_id": "a1b2c3d4",
  "status": "queued",
  "prompt": "ancient Sanxingdui high priest...",
  "progress": 0.0
}
```

**参数说明**:
- `identity` (必需): 大祭司 | 部落首领 | 贵族武士 | 女神巫觋
- `scene` (必需): 青铜神树祭坛 | 王宫大殿 | 河畔祭祀坑 | 古蜀神庙
- `item` (必需): 金杖 | 玉璋 | 象牙器 | 青铜面具
- `style` (必需): 史诗油画风 | 工笔重彩风 | 赛博朋克风 | 水墨晕染风
- `custom_prompt` (可选): 自定义提示词，会覆盖自动生成的 Prompt
- `seed` (可选): 随机种子，不指定会自动生成

---

### 2. 获取任务状态

**端点**: `GET /api/ai-restoration/status/{task_id}`

**响应**:
```json
{
  "success": true,
  "task_id": "a1b2c3d4",
  "status": "completed",
  "image_url": "/api/images/sanxingdui_photo_lora_12345.png",
  "prompt": "ancient Sanxingdui high priest...",
  "progress": 100.0
}
```

**状态值**:
- `queued`: 等待中
- `generating`: 生成中
- `completed`: 已完成
- `failed`: 失败

---

### 3. 获取图像

**端点**: `GET /api/images/{filename}`

**示例**:
```
GET /api/images/sanxingdui_photo_lora_12345.png
```

---

### 4. 获取参数列表

**端点**: `GET /api/ai-restoration/parameters`

**响应**:
```json
{
  "identity": ["大祭司", "部落首领", "贵族武士", "女神巫觋"],
  "scene": ["青铜神树祭坛", "王宫大殿", "河畔祭祀坑", "古蜀神庙"],
  "item": ["金杖", "玉璋", "象牙器", "青铜面具"],
  "style": ["史诗油画风", "工笔重彩风", "赛博朋克风", "水墨晕染风"]
}
```

---

### 5. 健康检查

**端点**: `GET /api/ai-restoration/health`

**响应**:
```json
{
  "status": "ok",
  "backend": "fastapi",
  "method": "script_workflow_modification",
  "comfyui": "connected",
  "workflow_loaded": true
}
```

---

## 🔄 工作流程

```
1. 用户发起请求 (POST /api/ai-restoration/generate)
   ↓
2. 后端验证参数 ✓
   ↓
3. 构建英文 Prompt
   ├─ 身份描述 + 物品描述 + 场景描述 + 风格描述
   └─ 质量标签 ("masterpiece, best quality, ...")
   ↓
4. 修改工作流
   ├─ 节点 9: 设置正向提示词
   ├─ 节点 6: 设置随机种子
   └─ 保持其他参数不变
   ↓
5. 转换为 ComfyUI API 格式
   ├─ nodes: Dict[str, NodeData]
   ├─ 链接转换: [node_id, slot_index]
   └─ widgets_values 完整性检查
   ↓
6. 后台提交任务
   ├─ POST /prompt to ComfyUI
   └─ 获得 prompt_id
   ↓
7. 轮询结果
   ├─ 每 2 秒检查一次 /history/{prompt_id}
   ├─ 最多等待 10 分钟
   └─ 更新 progress
   ↓
8. 返回结果
   ├─ status: "completed"
   ├─ image_url: "/api/images/..."
   └─ 前端可直接访问图像
```

---

## 💻 使用示例

### Python 示例

```python
import requests
import time

BASE_URL = "http://localhost:8000"

# 1. 生成图像
response = requests.post(
    f"{BASE_URL}/api/ai-restoration/generate",
    json={
        "identity": "大祭司",
        "scene": "青铜神树祭坛",
        "item": "金杖",
        "style": "史诗油画风"
    }
)

result = response.json()
task_id = result["task_id"]
print(f"🚀 任务已创建: {task_id}")
print(f"📝 提示词: {result['prompt'][:80]}...")

# 2. 轮询结果
while True:
    status_response = requests.get(f"{BASE_URL}/api/ai-restoration/status/{task_id}")
    status = status_response.json()
    
    if status["status"] == "completed":
        print(f"✅ 完成！")
        print(f"📸 图像: {status['image_url']}")
        break
    elif status["status"] == "failed":
        print(f"❌ 失败: {status['error']}")
        break
    else:
        print(f"⏳ 进行中 {status['progress']:.0f}%...")
        time.sleep(2)

# 3. 下载图像
image_response = requests.get(f"{BASE_URL}{status['image_url']}")
with open("result.png", "wb") as f:
    f.write(image_response.content)
```

### cURL 示例

```bash
# 生成图像
curl -X POST http://localhost:8000/api/ai-restoration/generate \
  -H "Content-Type: application/json" \
  -d '{
    "identity": "大祭司",
    "scene": "青铜神树祭坛",
    "item": "金杖",
    "style": "史诗油画风"
  }'

# 获取状态
curl http://localhost:8000/api/ai-restoration/status/a1b2c3d4

# 获取图像
curl http://localhost:8000/api/images/sanxingdui_photo_lora_12345.png -o result.png
```

### JavaScript/Node.js 示例

```javascript
const BASE_URL = "http://localhost:8000";

async function generateImage() {
  // 1. 发起请求
  const response = await fetch(`${BASE_URL}/api/ai-restoration/generate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      identity: "大祭司",
      scene: "青铜神树祭坛",
      item: "金杖",
      style: "史诗油画风"
    })
  });
  
  const result = await response.json();
  const taskId = result.task_id;
  
  console.log(`🚀 任务: ${taskId}`);
  
  // 2. 轮询结果
  let status;
  while (true) {
    const statusResp = await fetch(`${BASE_URL}/api/ai-restoration/status/${taskId}`);
    status = await statusResp.json();
    
    if (status.status === "completed") {
      console.log(`✅ 完成！`);
      console.log(`📸 图像: ${status.image_url}`);
      break;
    } else if (status.status === "failed") {
      console.log(`❌ 失败: ${status.error}`);
      break;
    } else {
      console.log(`⏳ 进行中 ${status.progress.toFixed(0)}%...`);
      await new Promise(r => setTimeout(r, 2000));
    }
  }
}

generateImage();
```

---

## 🔧 配置

**环境变量**:
```bash
# 设置 ComfyUI 服务器地址
export COMFYUI_SERVER="http://localhost:8188"

# 启动后端
python fastapi_backend.py
```

**工作流文件位置**:
```
workflows/三星堆_lora.json
```

---

## 📊 参数组合

总共支持 **4 × 4 × 4 × 4 = 256 种** 参数组合

### 身份 (4 种)
- 大祭司: 高级祭司，神圣气息
- 部落首领: 权力象征，龙纹装饰
- 贵族武士: 青铜甲胄，贵族气质
- 女神巫觋: 神秘祭司，精神力量

### 场景 (4 种)
- 青铜神树祭坛: 传奇青铜树，神秘仪式
- 王宫大殿: 皇家宫殿，金碧辉煌
- 河畔祭祀坑: 河边祭坛，古老遗迹
- 古蜀神庙: 古蜀寺庙，精神空间

### 物品 (4 种)
- 金杖: 权力象征的黄金权杖
- 玉璋: 龙纹的圣玉牌
- 象牙器: 象征仪式的象牙器皿
- 青铜面具: 神圣的青铜面具

### 风格 (4 种)
- 史诗油画风: 戏剧性照明，油画质感
- 工笔重彩风: 中国绘画风格，细节丰富
- 赛博朋克风: 霓虹灯光，未来科技混合
- 水墨晕染风: 中国水墨，艺术极简

---

## 🐛 故障排查

### 问题 1: ComfyUI 连接失败

```
错误: ComfyUI 未运行
```

**解决方案**:
1. 确保 ComfyUI 已启动
2. 检查 http://localhost:8188/system_stats 是否可访问
3. 检查防火墙设置

### 问题 2: 工作流加载失败

```
错误: 工作流加载失败
```

**解决方案**:
1. 检查文件路径: `workflows/三星堆_lora.json`
2. 验证文件格式是否正确 JSON
3. 检查文件权限

### 问题 3: 生成超时

```
错误: 生成超时（超过 10 分钟）
```

**原因**:
- GPU 性能不足
- ComfyUI 队列已满
- 模型加载缓慢

**解决方案**:
1. 减少并发请求数
2. 检查 GPU 使用情况
3. 增加超时时间

### 问题 4: API 返回 400 错误

```
错误: 无效的身份
```

**原因**:
- 参数拼写错误
- 参数不在允许列表中

**解决方案**:
1. 调用 `/api/ai-restoration/parameters` 获取有效参数
2. 检查参数是否完全匹配

---

## 📈 性能指标

- **平均生成时间**: 1-3 分钟
- **图像分辨率**: 1024×1024 像素
- **模型**: SDXL 1.0 + LoRA
- **采样步数**: 28 步
- **CFG Scale**: 6.5
- **采样器**: DPM++ 2M
- **调度器**: Karras

---

## 🔐 安全性

✅ **已实现**:
- CORS 中间件: 允许跨域请求
- 路径安全检查: 防止路径遍历
- 参数验证: 白名单验证
- 错误隐藏: 敏感信息不泄露

⚠️ **建议**:
- 部署时启用 HTTPS
- 添加 API 密钥认证
- 限制请求速率
- 监控磁盘空间

---

## 📝 日志

后端会输出详细的日志:

```
🚀 新建任务: a1b2c3d4
   身份: 大祭司
   场景: 青铜神树祭坛
   物品: 金杖
   风格: 史诗油画风
   ✅ 修改节点 9 (正向提示词)
   ✅ 修改节点 6 (种子: 1234567890)
   📤 提交到 ComfyUI: http://localhost:8188/prompt
   ✅ 提交成功, Prompt ID: abc123def456

✅ 任务 a1b2c3d4 完成
   图像: sanxingdui_photo_lora_12345.png
   URL: /api/images/sanxingdui_photo_lora_12345.png
```

---

## 📚 相关文件

- `backend/fastapi_backend.py` - 主要后端代码
- `workflows/三星堆_lora.json` - 工作流定义
- `start.sh` - 启动脚本
- `COMFYUI_BACKEND_SOLUTION.md` - 设计文档

---

## 🎯 后续优化

- [ ] 实现 WebSocket 实时进度推送
- [ ] 添加生成历史记录
- [ ] 实现批量生成
- [ ] 添加图像后处理
- [ ] 实现结果缓存
- [ ] 添加用户认证
- [ ] 实现队列管理
- [ ] 性能监控仪表板

---

## ✅ 完成清单

✅ 工作流加载和修改
✅ API 格式转换
✅ ComfyUI 提交
✅ 结果轮询
✅ FastAPI 接口
✅ 后台任务处理
✅ 错误处理
✅ 参数验证
✅ 完整文档
✅ 使用示例

🎉 系统已就绪！

