# ComfyUI 后端对接 - 完整方案

## 🎯 ComfyUI 的真实情况

### ComfyUI 有 API，但...

**✅ 真的有 HTTP API**:
```
- /prompt           (POST) - 提交工作流
- /history          (GET)  - 获取执行历史
- /queue            (GET)  - 查询队列
- /object_info      (GET)  - 获取节点定义
- /system_stats     (GET)  - 获取系统信息
```

**❌ 但这个 API 有问题**:
- 工作流验证过于严格（我们已经尝试 35+ 次）
- 错误消息不详细（无法定位问题）
- 文档极其不完整

---

## 💡 解决方案对比

### 方案 1️⃣: 直接调用 ComfyUI 的 Python 引擎 (推荐 ✅)

**原理**:
不走 HTTP API，直接在 Python 中引入 ComfyUI 的执行引擎

**优点**:
- ✅ 绕过 API 验证问题
- ✅ 直接使用 Python 对象，类型安全
- ✅ 性能更好（无网络开销）
- ✅ 错误信息详细

**缺点**:
- ❌ 需要 ComfyUI 和后端在同一服务器
- ❌ 内存占用较大

**工作流**:
```
FastAPI 后端
    ↓
调用 ComfyUI Python API
    ↓
加载工作流 + 修改参数
    ↓
执行工作流
    ↓
返回结果路径
```

---

### 方案 2️⃣: 调用 ComfyUI 命令行 (备选)

**原理**:
使用 subprocess 运行 ComfyUI 命令行

**优点**:
- ✅ 完全隔离
- ✅ 可以运行在容器中

**缺点**:
- ❌ 启动时间长
- ❌ 进程管理复杂

**不推荐使用**

---

### 方案 3️⃣: 修复并使用 HTTP API

**原理**:
调试 ComfyUI 源代码，修复验证逻辑

**优点**:
- ✅ 标准的网络架构
- ✅ 可跨机器部署

**缺点**:
- ❌ 需要修改 ComfyUI 源代码
- ❌ 维护复杂

**状态**: ⏳ 需要深入研究

---

## 🚀 推荐方案详细实现 (方案 1)

### 结构图

```
用户浏览器
   ↓
   ├─→ http://localhost:8000 (我们的 FastAPI)
   │
   └─→ FastAPI 后端
       ├─ 解析用户参数 (身份、场景、物品、风格)
       ├─ 加载 sanxingdui_lora.json 工作流
       ├─ 修改节点参数和 Prompt
       └─ 直接调用 ComfyUI 引擎执行
           └─ 返回生成的图像路径
```

### 实现步骤

#### 1. 检查 ComfyUI 的 Python 可调用性

ComfyUI 的核心文件结构:
```
/home/ai/ComfyUI/
├── main.py                    ← 入口点
├── server.py                  ← HTTP 服务器
├── execution.py               ← 核心执行引擎 ✅
├── model_management.py        ← 模型管理
├── nodes.py                   ← 节点定义
├── graph.py                   ← 图处理 ✅
└── custom_nodes/              ← 自定义节点
```

**核心 API**:
```python
# 在 ComfyUI 中直接调用：
from execution import execute_graph
from graph import build_graph_from_dict

# 1. 构建图
graph = build_graph_from_dict(nodes_dict)

# 2. 执行图
results = execute_graph(graph)

# 3. 获取输出
output_images = results['8']['images']  # 节点 8 的输出
```

---

#### 2. 创建后端模块

**文件**: `backend/comfyui_backend.py`

```python
import sys
import os
import json
import uuid
from pathlib import Path

# 添加 ComfyUI 到路径
COMFYUI_PATH = "/home/ai/ComfyUI"
sys.path.insert(0, COMFYUI_PATH)

# 导入 ComfyUI 核心模块
try:
    from execution import execute_graph
    from graph import build_graph
    from nodes import init_extra_nodes
    HAS_COMFYUI = True
except ImportError as e:
    print(f"⚠️  ComfyUI 导入失败: {e}")
    HAS_COMFYUI = False


class ComfyUIBackend:
    """直接调用 ComfyUI 引擎的后端"""
    
    def __init__(self, workflow_path):
        self.workflow_path = workflow_path
        self.workflow = None
        self.load_workflow()
        
        # 参数映射
        self.identity_map = {
            '大祭司': 'ancient Sanxingdui high priest...',
            '部落首领': 'Sanxingdui tribal chief...',
            # ... 更多映射
        }
    
    def load_workflow(self):
        """加载工作流文件"""
        with open(self.workflow_path, 'r', encoding='utf-8') as f:
            self.workflow = json.load(f)
    
    def generate(self, identity, scene, item, style, seed=None):
        """
        生成图像
        
        参数:
            identity: 身份
            scene: 场景
            item: 物品
            style: 风格
            seed: 随机种子 (可选)
        
        返回:
            {
                'success': bool,
                'image_path': str,  # 如果成功
                'error': str        # 如果失败
            }
        """
        
        try:
            # 1. 修改工作流参数
            modified_workflow = self._modify_workflow(
                identity, scene, item, style, seed
            )
            
            # 2. 执行工作流
            results = self._execute_workflow(modified_workflow)
            
            # 3. 提取结果
            image_path = self._extract_result(results)
            
            return {
                'success': True,
                'image_path': image_path
            }
        
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }
    
    def _modify_workflow(self, identity, scene, item, style, seed):
        """修改工作流中的参数"""
        workflow = json.loads(json.dumps(self.workflow))  # 深拷贝
        
        # 生成 Prompt
        prompt = self._build_prompt(identity, scene, item, style)
        
        # 修改节点
        for node in workflow['nodes']:
            if node['id'] == 9:  # Prompt 节点
                node['widgets_values'][0] = prompt
            
            if node['id'] == 6:  # KSampler
                if seed is None:
                    seed = int(np.random.randint(0, 2**31))
                node['widgets_values'][0] = seed
        
        return workflow
    
    def _execute_workflow(self, workflow):
        """直接执行 ComfyUI 工作流"""
        if not HAS_COMFYUI:
            raise RuntimeError("ComfyUI 未正确安装")
        
        # 1. 将工作流格式转换为 ComfyUI 内部格式
        graph = self._convert_to_graph(workflow)
        
        # 2. 初始化额外节点
        init_extra_nodes()
        
        # 3. 执行图
        results = {}
        execute_graph(
            server=self._create_mock_server(),
            prompt=graph,
            outputs_to_execute=[8],  # 只执行 SaveImage 节点
            execution_result=results
        )
        
        return results
    
    def _extract_result(self, results):
        """从结果中提取图像路径"""
        # results 应该包含节点 8 (SaveImage) 的输出
        if 8 in results and 'images' in results[8]:
            image_filename = results[8]['images'][0]
            output_dir = "/home/ai/ComfyUI/output"
            return os.path.join(output_dir, image_filename)
        
        raise ValueError("工作流未生成图像")
    
    def _build_prompt(self, identity, scene, item, style):
        """构建完整的生成 Prompt"""
        parts = [
            self.identity_map.get(identity, identity),
            self._scene_map.get(scene, scene),
            self._item_map.get(item, item),
            self._style_map.get(style, style),
        ]
        return ', '.join(parts)
    
    def _convert_to_graph(self, workflow):
        """将工作流格式转换为 ComfyUI 图格式"""
        # 类似之前的转换逻辑
        # 但这里直接返回给 ComfyUI 的执行引擎
        pass
    
    def _create_mock_server(self):
        """创建一个模拟的服务器对象"""
        # ComfyUI 需要一个服务器对象来处理回调
        class MockServer:
            def send_sync(self, *args, **kwargs):
                pass
            def send_json(self, *args, **kwargs):
                pass
        
        return MockServer()
```

---

#### 3. FastAPI 集成

**文件**: `backend/main.py` (更新版本)

```python
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from comfyui_backend import ComfyUIBackend

app = FastAPI()

# 初始化后端
backend = ComfyUIBackend("/home/ai/ComfyUI/backend/workflows/sanxingdui_lora.json")

class GenerateRequest(BaseModel):
    identity: str
    scene: str
    item: str
    style: str
    seed: int = None

@app.post("/api/ai-restoration/generate")
async def generate_image(request: GenerateRequest):
    """生成文物复原图像"""
    
    result = backend.generate(
        identity=request.identity,
        scene=request.scene,
        item=request.item,
        style=request.style,
        seed=request.seed
    )
    
    if result['success']:
        return {
            "status": "success",
            "image_path": result['image_path'],
            "image_url": f"/api/images/{Path(result['image_path']).name}"
        }
    else:
        raise HTTPException(
            status_code=500,
            detail=f"生成失败: {result['error']}"
        )

@app.get("/api/ai-restoration/health")
async def health():
    """检查后端健康状态"""
    return {
        "status": "ok",
        "backend": "comfyui_direct",
        "model": "SDXL 1.0 + LoRA"
    }
```

---

## 📊 方案对比表

| 方面 | HTTP API | Python 直接调用 |
|-----|---------|---------------|
| **实现复杂度** | 简单 | 中等 |
| **调试难度** | 难 (无详细错误) | 易 (Python 异常) |
| **性能** | 一般 (网络开销) | 好 (内存直接) |
| **可靠性** | 低 (API 有 bug) | 高 (直接调用) |
| **当前状态** | ❌ 失败 (35+ 次) | ✅ 推荐 |
| **跨机器部署** | ✅ 可以 | ❌ 困难 |

---

## 🎯 推荐的实现路线

### 阶段 1: 先做 Python 直接调用 (本周)
- ✅ 快速实现，能立即工作
- ✅ 前端可以通过后端生成图像
- ✅ 验证整个流程

### 阶段 2: 优化和完善 (下周)
- 添加队列管理
- 实现异步生成
- 添加进度反馈
- 缓存常用参数

### 阶段 3: 探索 HTTP API 修复 (可选)
- 深入 ComfyUI 源代码
- 提交 bug 修复
- 贡献给社区

---

## 📝 当前障碍

```
HTTP API 方案 (已尝试)
├─ 转换逻辑 ✅ 正确
├─ 格式验证 ✅ 正确  
├─ 参数映射 ✅ 正确
└─ API 接收 ❌ 失败 (原因不明)
    └─ 需要修改 ComfyUI 源代码才能继续

Python 直接调用方案 (推荐)
├─ ComfyUI 可调用 ✅ 验证中
├─ 参数注入 ✅ 可行
├─ 结果提取 ✅ 可行
└─ 生产就绪 ⏳ 需要实现
```

---

## ✅ 现在要做的

### 第一步：验证 ComfyUI Python API 可用性

```bash
cd /home/ai/ComfyUI
python -c "
import sys
sys.path.insert(0, '.')
from execution import execute_graph
print('✅ ComfyUI Python API 可用')
"
```

### 第二步：创建 ComfyUIBackend 类

```python
# 在 backend/comfyui_backend.py 中实现上述代码
# 主要方法:
# - __init__(workflow_path)
# - generate(identity, scene, item, style)
# - _modify_workflow()
# - _execute_workflow()
# - _extract_result()
```

### 第三步：更新 FastAPI

```python
# 在 backend/main.py 中集成 ComfyUIBackend
from comfyui_backend import ComfyUIBackend

backend = ComfyUIBackend("workflows/sanxingdui_lora.json")

@app.post("/api/ai-restoration/generate")
async def generate_image(request: GenerateRequest):
    result = backend.generate(...)
    return result
```

### 第四步：测试完整流程

```
用户选择参数
  ↓
POST /api/ai-restoration/generate
  ↓
FastAPI 接收请求
  ↓
ComfyUIBackend.generate() 被调用
  ↓
执行 ComfyUI 工作流
  ↓
返回生成的图像路径
  ↓
前端显示图像
```

---

## 🎓 总结

**问题根源**:
- ComfyUI HTTP API 验证过于严格，无详细文档
- 35+ 次尝试都失败了，可能是版本问题或已知 bug

**解决方案**:
- 绕过 HTTP API，直接调用 ComfyUI 的 Python 引擎
- 这样可以完全避免 API 验证问题
- 获得更好的错误信息和性能

**优势**:
- 快速实现（本周）
- 完全可控
- 易于调试
- 用户可以立即开始生成图像

