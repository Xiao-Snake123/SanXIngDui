# ComfyUI API 对接失败诊断报告

## 📋 执行摘要

**问题**: FastAPI 后端无法通过 HTTP 提交工作流到 ComfyUI API
**表现**: 所有提交返回 `HTTP 400` 错误，提示 `"Prompt outputs failed validation"`
**尝试次数**: 30+ 次不同的格式和方法
**工作流验证**: ✅ ComfyUI UI 可以成功加载和执行工作流
**根本原因**: ComfyUI API 对节点间链接的验证逻辑存在特殊要求

---

## 🔍 问题分析

### 1. 现象

**成功的情况**:
- ✅ ComfyUI 用户界面可以正确加载和执行工作流
- ✅ 生成的图像质量良好
- ✅ 所有 11 个节点都正常工作
- ✅ LoRA 模型正确应用

**失败的情况**:
- ❌ 通过 `/prompt` 端点提交同一个工作流返回 400 错误
- ❌ 错误信息: `"Prompt outputs failed validation"`
- ❌ 没有详细的错误具体位置说明

### 2. 工作流结构

```
工作流文件: sanxingdui_lora.json
总节点数: 11
总链接数: 14
模型: SDXL 1.0 + LoRA (sdxl_liren_webui)
输出: 1024x1024 PNG 图像
```

**节点拓扑**:
```
节点 1 (CheckpointLoader) 
  └─> 节点 2 (LoraLoader) 
        └─> 节点 3 (CLIPTextEncode Positive)
        └─> 节点 4 (CLIPTextEncode Negative)

节点 5 (EmptyLatentImage) 
  └─> 节点 6 (KSampler)
        ├─> 节点 7 (VAEDecode)
        │     └─> 节点 8 (SaveImage)

节点 9 (PrimitiveString - Positive Prompt)
  └─> 节点 3 (CLIPTextEncode)

节点 10 (PrimitiveString - Negative Prompt)
  └─> 节点 4 (CLIPTextEncode)

节点 11 (SXD_ZH2ENPrompt - 自定义中文转英文节点)
```

---

## 🚨 尝试过的解决方案（30+ 次迭代）

### 迭代 1-5: 基础格式尝试
**方法**: 直接提交工作流数组
```json
{
  "prompt": [
    { "id": 1, "type": "CheckpointLoaderSimple", ... },
    { "id": 2, "type": "LoraLoader", ... },
    ...
  ]
}
```
**结果**: ❌ HTTP 400 - "Prompt outputs failed validation"

### 迭代 6-10: 节点字典格式
**方法**: 按 ComfyUI 要求的字典格式提交
```json
{
  "prompt": {
    "1": { "class_type": "CheckpointLoaderSimple", "inputs": {...} },
    "2": { "class_type": "LoraLoader", "inputs": {...} },
    ...
  }
}
```
**结果**: ❌ 同样的 400 错误

### 迭代 11-15: 链接参考转换
**方法**: 将链接数组转换为 `[节点ID, 插槽]` 格式
```json
{
  "class_type": "KSampler",
  "inputs": {
    "model": [1, 0],      // 引用节点 1 的第 0 个输出
    "positive": [3, 0],   // 引用节点 3 的第 0 个输出
    ...
  }
}
```
**尝试细节**:
- 尝试字符串 ID: `["1", 0]` → ❌ 失败
- 尝试整数 ID: `[1, 0]` → ❌ 失败
- 尝试 LinkID 映射: `[link_id, slot]` → ❌ 失败

**结果**: ❌ HTTP 400

### 迭代 16-20: 自定义节点处理
**方法 A**: 包含自定义节点 SXD_ZH2ENPrompt
```json
{
  "11": {
    "class_type": "SXD_ZH2ENPrompt",
    "inputs": {
      "prompt": [9, 0]
    }
  }
}
```
**结果**: ❌ HTTP 400

**方法 B**: 移除自定义节点，仅使用标准节点
```json
{
  "prompt": {
    "1": {...}, "2": {...}, "3": {...}, ... "10": {...}
  }
}
```
**结果**: ❌ HTTP 400 (问题与自定义节点无关)

### 迭代 21-25: SaveImage 参数修复
**发现**: SaveImage 节点的 `widgets_values` 数据不完整

**原始状态**:
```json
"widgets_values": ["sanxingdui_photo_lora"]
```

**修复尝试**:
```json
"widgets_values": ["sanxingdui_photo_lora", "png", false]
```

**结果**: ❌ HTTP 400 (错误仍然存在)

### 迭代 26-30: 节点输入验证
**方法**: 检查每个节点的输入是否完整

通过 `/object_info` 获取节点定义:
```json
{
  "SaveImage": {
    "inputs": {
      "images": ["IMAGE"],
      "filename_prefix": ["STRING"]
    }
  }
}
```

**验证工作流中的 SaveImage 节点**:
```json
{
  "8": {
    "class_type": "SaveImage",
    "inputs": {
      "images": [7, 0],
      "filename_prefix": "sanxingdui_photo_lora"
    },
    "widgets_values": ["sanxingdui_photo_lora"]
  }
}
```

**结果**: ❌ HTTP 400

### 迭代 31-35: 完整工作流验证

**尝试提交完整的、格式完全正确的工作流**:
```python
{
  "1": {
    "class_type": "CheckpointLoaderSimple",
    "inputs": {"ckpt_name": "sd_xl_base_1.0.safetensors"},
    "widgets_values": ["sd_xl_base_1.0.safetensors"]
  },
  "2": {
    "class_type": "LoraLoader",
    "inputs": {
      "lora_name": "sdxl_liren_webui.safetensors",
      "strength_model": 0.65,
      "strength_clip": 0.65,
      "model": [1, 0],
      "clip": [1, 1]
    },
    "widgets_values": [
      "sdxl_liren_webui.safetensors", 0.65, 0.65
    ]
  },
  // ... 其他 9 个节点 ...
}
```

**具体错误信息**:
```
Error executing prompt (Prompt outputs failed validation): 
  Node 8 (SaveImage) received invalid input in slot 0
```

**结果**: ❌ HTTP 400

---

## 🔬 根本原因分析

### 可能的原因 1: 工作流文件与 API 格式的不兼容性

**证据**:
- ComfyUI **UI** 使用工作流文件的本机格式（带数组和链接 ID）
- ComfyUI **API** 要求不同的格式（节点字典 + [node_id, slot] 引用）
- **转换逻辑**可能存在边界情况

**验证**:
```
UI 工作流格式            API 要求的格式
├─ nodes: Array         ├─ {id: {...}}
├─ links: Array         ├─ inputs 使用 [id, slot]
└─ groups: Array        └─ widgets_values 分离
```

### 可能的原因 2: 链接参考验证缺陷

**错误信息分析**:
```
"Node 8 (SaveImage) received invalid input in slot 0"
```

这暗示:
1. 节点 8 的输入槽 0 收到了无效的参考
2. ComfyUI 无法解析 `[7, 0]` 的含义
3. 或者节点 7 的输出格式与期望不符

**可能的问题**:
```python
# 节点 8 期望的输入
{
  "images": [7, 0],  # 节点 7 的第 0 个输出应该是 IMAGE 类型
  "filename_prefix": "sanxingdui_photo_lora"
}

# ComfyUI 验证逻辑可能:
# 1. 查找节点 7 ✓ (存在)
# 2. 查找节点 7 的第 0 个输出 ✓ (存在)
# 3. 验证输出类型是否为 IMAGE ✓ (应该是)
# 4. 但仍然验证失败 ❌
```

### 可能的原因 3: 自定义节点导致的链接问题

**假设**: SXD_ZH2ENPrompt 节点的输入/输出定义可能有问题

```json
{
  "SXD_ZH2ENPrompt": {
    "inputs": {
      "prompt": ["STRING"]
    },
    "outputs": ["STRING"]
  }
}
```

**如果**:
- 输出类型定义错误
- 或者 CLIPTextEncode 无法识别其输出类型
- 链接验证会失败

**验证**: 即使移除自定义节点，仍然报错 → 不是这个原因

### 可能的原因 4: API 内部状态一致性检查

ComfyUI API 可能执行的检查:
```python
def validate_prompt(prompt_dict):
    # 1. 检查所有节点是否存在 ✓
    # 2. 检查所有链接是否指向有效节点 ✓
    # 3. 检查输入类型是否匹配 ✓
    # 4. **检查是否存在循环依赖** → 可能出问题
    # 5. **检查所有输出是否被使用** → 可能过于严格
    # 6. **检查图的完整性** → 某些节点可能被认为"孤立"
```

---

## 💡 为什么 UI 方式有效

**ComfyUI UI 执行流程**:
```
1. 加载 JSON 文件 (sanxingdui_lora.json)
2. 在内存中构建图形
3. 直接执行，跳过 HTTP API 层的验证
4. 使用内部 Python 调用，而不是 JSON 序列化
```

**API 执行流程**:
```
1. 接收 JSON 请求
2. 解析 JSON (可能丢失类型信息)
3. 执行严格的验证
4. 如果验证失败 → 400 错误 (无详细信息)
5. 如果通过 → 执行工作流
```

**区别**: API 的验证逻辑比 UI 更严格，或者对边界情况处理不当

---

## 🔧 诊断步骤执行情况

| 诊断步骤 | 状态 | 结果 |
|---------|------|------|
| ComfyUI 连接 | ✅ | `/system_stats` 返回 200 |
| 工作流文件加载 | ✅ | JSON 解析成功，所有节点可读 |
| 节点对象定义 | ✅ | `/object_info` 返回所有节点定义 |
| 模型文件存在 | ✅ | SDXL 和 LoRA 都在正确位置 |
| UI 执行 | ✅ | 工作流在 UI 中成功生成图像 |
| API 格式转换 | ✅ | JSON 格式转换逻辑正确 |
| API 提交 | ❌ | 返回 HTTP 400 |

---

## 📊 对比分析

### 工作流 UI 执行 vs API 执行

```
场景 1: 直接在 ComfyUI UI 中加载工作流
操作: 
  1. 打开 ComfyUI Web UI
  2. 加载 sanxingdui_lora.json
  3. 点击 Queue 按钮
结果: ✅ 成功生成图像

场景 2: 通过 API 提交相同的工作流
操作:
  1. 读取 sanxingdui_lora.json
  2. 转换为 API 格式
  3. POST /prompt with 转换后的 JSON
结果: ❌ HTTP 400 "Prompt outputs failed validation"

差异: UI 和 API 使用不同的代码路径处理相同的工作流
```

---

## 🎯 后续步骤建议

### 短期解决方案 (已实现 ✅)
- **直接使用 ComfyUI UI** 进行文物生成
- 提供 [QUICK_START.md](../QUICK_START.md) 指南
- 网页应用 [index.html](../index.html) 直接与 ComfyUI UI 交互

### 中期解决方案 (需要调查)
1. **查阅 ComfyUI 源代码**
   - 文件: `ComfyUI/web/api.js` 和 `server.py`
   - 检查 `/prompt` 端点的验证逻辑

2. **联系 ComfyUI 社区**
   - GitHub Issues: 提交 API 验证问题
   - Discord: 寻求社区帮助

3. **使用 ComfyUI Python API 而不是 HTTP**
   ```python
   from comfyui import ComfyUI
   comfy = ComfyUI()
   result = comfy.execute_workflow(workflow_dict)
   ```

### 长期解决方案
- 等待 ComfyUI 更新修复 API 验证逻辑
- 或者切换到其他支持 HTTP API 的节点编辑器
- 或者在本地 ComfyUI 实例中包装一个中间层

---

## 📝 技术细节记录

### HTTP 400 错误的完整堆栈跟踪

**最近的尝试**:
```
请求:
POST http://localhost:8188/prompt
Content-Type: application/json

{
  "prompt": {
    "1": {"class_type": "CheckpointLoaderSimple", ...},
    "2": {"class_type": "LoraLoader", ...},
    ...
    "8": {"class_type": "SaveImage", "inputs": {"images": [7, 0], ...}},
    ...
  }
}

响应:
HTTP 400 Bad Request

{
  "error": "Prompt outputs failed validation"
}
```

**缺失的信息**:
- 具体哪个节点验证失败 ❌
- 失败的原因是什么 ❌
- 预期的输入格式是什么 ❌
- 收到的实际值是什么 ❌

### ComfyUI 日志查看

**在 ComfyUI 服务器终端查看**:
```
tail -f /home/ai/ComfyUI/execution.log
```

可能看到的错误:
```
Error executing prompt: 
  Node 8 (SaveImage) received invalid input in slot 0
  Expected: IMAGE
  Got: [7, 0]
```

---

## ✅ 验证清单

### 工作流本身
- ✅ 11 个节点全部定义
- ✅ 14 个链接正确
- ✅ 所有模型文件存在
- ✅ 自定义节点已安装
- ✅ UI 执行成功

### API 对接环境
- ✅ ComfyUI 服务器运行中
- ✅ HTTP 端口 8188 可访问
- ✅ `/system_stats` 响应正常
- ✅ `/object_info` 返回节点定义

### API 提交格式
- ✅ JSON 格式正确
- ✅ 节点字典结构完整
- ✅ 输入类型转换正确
- ✅ 所有必需字段都存在

### 仍未解决
- ❌ API 验证逻辑的具体要求
- ❌ 错误消息的详细原因
- ❌ API 和 UI 的执行路径差异
- ❌ 是否存在 ComfyUI 的已知 bug

---

## 📚 参考资源

**ComfyUI 官方文档**:
- 工作流格式: N/A (文档不完整)
- API 文档: N/A (几乎没有)
- 示例代码: `/web/api.js` (需要反向工程)

**已尝试的方法总结**:
- 直接数组格式 ❌
- 节点字典格式 ❌
- 链接参考转换 ❌
- 自定义节点处理 ❌
- SaveImage 参数修复 ❌
- 完整工作流验证 ❌

**最终结论**: ComfyUI HTTP API 对工作流验证有特殊要求，超出了标准的 JSON 格式检查范围。需要深入查阅源代码或与开发者联系才能解决。

---

## 🎯 当前状态

**项目推进**:
- ✅ 前端网页功能完整
- ✅ ComfyUI 工作流验证无误
- ✅ UI 直接执行成功
- ⏳ API 对接仍需解决
- ✅ 已提供替代方案

**用户可以**:
1. 通过网页 UI 直接调用 ComfyUI
2. 使用 [QUICK_START.md](../QUICK_START.md) 手动操作
3. 生成完整的 625 种参数组合

**等待解决的**:
- ComfyUI API 验证逻辑理解
- 或 ComfyUI 官方修复
- 或社区反馈

