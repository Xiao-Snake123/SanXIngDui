# 三星堆项目 Windows 服务器部署文档

本文档适用于将当前 React + Vite 前端项目部署到 Windows Server。当前项目不再依赖 ComfyUI，本地服务器只需要负责前端静态资源和两个 API 反向代理：

- 图片生成：DashScope `z-image-turbo`
- 聊天助手：`https://sxdapi.aitrais.cn/v1/chat-messages`

## 一、需要下载的环境

### 必装

1. Node.js LTS
   - 推荐版本：Node.js 20 LTS 或 22 LTS
   - 下载地址：https://nodejs.org/
   - 安装时勾选 `Add to PATH`
   - 用途：安装依赖、执行 `npm run build`

2. Git for Windows
   - 下载地址：https://git-scm.com/download/win
   - 用途：拉取代码、更新代码

3. Nginx for Windows，推荐生产部署；如果使用项目内置 Node 服务，可以不装
   - 下载地址：https://nginx.org/en/download.html
   - 下载 Stable version 的 Windows 压缩包
   - 用途：生产环境托管 `dist` 静态文件，并代理 `/dashscope`、`/dify`

### 可选

1. VS Code
   - 下载地址：https://code.visualstudio.com/
   - 用途：编辑 `.env.local`、`nginx.conf`

2. NSSM
   - 下载地址：https://nssm.cc/download
   - 用途：把 Nginx 或开发服务注册为 Windows 服务

3. 7-Zip
   - 下载地址：https://www.7-zip.org/
   - 用途：解压 Nginx、NSSM 压缩包

## 二、端口和网络要求

服务器需要能访问以下外部地址：

- `https://dashscope.aliyuncs.com`
- `https://sxdapi.aitrais.cn`

建议开放以下入站端口：

- `80`：HTTP 访问
- `443`：HTTPS 访问，如果配置证书
- `5173`：仅快速演示部署时使用

## 三、项目配置

在项目根目录创建或修改 `.env.local`：

```env
CHAT_APP_API_KEY=app-你的API_KEY
DASHSCOPE_API_KEY=sk-你的DashScope_API_KEY
```

注意：

- `.env.local` 只给 Vite 开发服务器读取。
- 正式生产部署使用 Nginx 时，API Key 需要写在 Nginx 反向代理配置里，不能放到前端代码。
- 不要把真实 API Key 提交到 Git 仓库。

## 四、推荐生产部署：项目内置 Node 服务

这种方式最适合当前项目：Node 服务会同时托管 `dist` 静态文件，并代理 `/dashscope`、`/dify`。不要再用 IIS 直接托管 `dist`，否则 `/dashscope` 会被 IIS 当成本地目录导致 404。

### 1. 配置 `.env.local`

在项目根目录创建或修改：

```env
CHAT_APP_API_KEY=app-你的API_KEY
DASHSCOPE_API_KEY=sk-你的DashScope_API_KEY
```

### 2. 安装依赖并构建

```powershell
cd "D:\SanxingduiRuinsPro"
npm install
npm run build
```

### 3. 启动生产服务

```powershell
npm run start
```

默认端口是 `8023`，访问：

```text
http://服务器IP:8023/
```

如需改端口：

```powershell
$env:PORT=80
npm run start
```

确认启动后，前端请求：

```text
/dashscope/api/v1/services/aigc/multimodal-generation/generation
/dify/v1/chat-messages
```

都会由 `server.mjs` 自动转发并添加 `Authorization`。

## 五、可选生产部署：Nginx 静态站点 + API 反向代理

### 1. 安装依赖

进入项目目录：

```powershell
cd "D:\SanxingduiRuinsPro"
npm install
```

如果安装慢，可以换国内镜像：

```powershell
npm config set registry https://registry.npmmirror.com
npm install
```

### 2. 构建前端

```powershell
npm run build
```

构建成功后会生成：

```text
dist/
```

### 3. 配置 Nginx

假设 Nginx 解压在：

```text
C:\nginx
```

打开：

```text
C:\nginx\conf\nginx.conf
```

替换为类似配置，注意把路径和 API Key 改成你自己的：

```nginx
worker_processes  1;

events {
    worker_connections  1024;
}

http {
    include       mime.types;
    default_type  application/octet-stream;
    sendfile      on;
    keepalive_timeout 65;

    server {
        listen 80;
        server_name _;

        root D:/SanxingduiRuinsPro/dist;
        index index.html;

        location / {
            try_files $uri $uri/ /index.html;
        }

        location /dashscope/ {
            proxy_pass https://dashscope.aliyuncs.com/;
            proxy_ssl_server_name on;
            proxy_set_header Host dashscope.aliyuncs.com;
            proxy_set_header Authorization "Bearer sk-你的DashScope_API_KEY";
            proxy_set_header Origin "";
            proxy_set_header Referer "";
        }

        location /dify/ {
            proxy_pass https://sxdapi.aitrais.cn/;
            proxy_ssl_server_name on;
            proxy_set_header Host sxdapi.aitrais.cn;
            proxy_set_header Authorization "Bearer app-你的API_KEY";
            proxy_set_header Origin "";
            proxy_set_header Referer "";
        }
    }
}
```

前端代码会请求：

```text
/dashscope/api/v1/services/aigc/multimodal-generation/generation
/dify/v1/chat-messages
```

经过 Nginx 后会分别转发到真实服务。

### 4. 启动 Nginx

```powershell
cd C:\nginx
start nginx
```

重新加载配置：

```powershell
nginx -s reload
```

停止 Nginx：

```powershell
nginx -s stop
```

### 5. 访问网站

浏览器打开：

```text
http://服务器IP/
```

## 六、快速演示部署：直接运行 Vite

这种方式适合内网演示和临时测试，不建议长期生产使用。

### 1. 配置 `.env.local`

```env
CHAT_APP_API_KEY=app-你的API_KEY
DASHSCOPE_API_KEY=sk-你的DashScope_API_KEY
```

### 2. 安装依赖

```powershell
cd "D:\SanxingduiRuinsPro"
npm install
```

### 3. 启动服务

```powershell
npm run dev -- --host 0.0.0.0 --port 5173
```

访问：

```text
http://服务器IP:5173/
```

## 七、验证功能

### 1. 页面验证

打开网站后检查：

- 首页是否正常打开
- 顶部导航是否正常
- AI 生图页面是否能提交任务
- 右下角小助手是否能回复问题

### 2. 聊天接口验证

在服务器 PowerShell 中执行，生产部署时走 Nginx：

```powershell
curl.exe -X POST "http://127.0.0.1/dify/v1/chat-messages" `
  -H "Content-Type: application/json" `
  -d "{\"inputs\":{},\"query\":\"三星堆是什么？\",\"response_mode\":\"blocking\",\"conversation_id\":\"\",\"user\":\"user_001\"}"
```

如果返回 401，检查 Nginx 中的：

```nginx
proxy_set_header Authorization "Bearer app-你的API_KEY";
```

### 3. 图片生成接口验证

生产部署时走 Nginx：

```powershell
curl.exe -X POST "http://127.0.0.1/dashscope/api/v1/services/aigc/multimodal-generation/generation" `
  -H "Content-Type: application/json" `
  -d "{\"model\":\"z-image-turbo\",\"input\":{\"messages\":[{\"role\":\"user\",\"content\":[{\"text\":\"三星堆青铜面具，博物馆灯光，真实照片风格\"}]}]},\"parameters\":{\"prompt_extend\":false,\"size\":\"1120*1440\"}}"
```

如果返回 401，检查 Nginx 中的：

```nginx
proxy_set_header Authorization "Bearer sk-你的DashScope_API_KEY";
```

## 八、常见问题

### 1. `npm` 不是内部或外部命令

说明 Node.js 没装好，或没有加入 PATH。

处理：

```powershell
node -v
npm -v
```

如果都失败，重新安装 Node.js LTS，并勾选 `Add to PATH`。

### 2. `npm install` 很慢或失败

切换 npm 镜像：

```powershell
npm config set registry https://registry.npmmirror.com
npm install
```

### 3. 页面刷新后 404

这是单页应用路由问题。Nginx 必须配置：

```nginx
location / {
    try_files $uri $uri/ /index.html;
}
```

### 4. 聊天或生图接口 401

检查：

- Nginx 配置里的 `Authorization` 是否写对
- API Key 前面是否有 `Bearer `
- 修改 Nginx 配置后是否执行了 `nginx -s reload`

### 5. 浏览器里接口 404

检查 Nginx 代理路径是否存在：

```nginx
location /dashscope/ { ... }
location /dify/ { ... }
```

### 6. 防火墙无法访问

在 Windows 防火墙中放行：

- `80`
- `443`
- `5173`，仅演示部署需要

也要检查云服务器安全组是否开放对应端口。

## 九、部署更新流程

每次更新代码后：

```powershell
cd "D:\SanxingduiRuinsPro"
git pull
npm install
npm run build
cd C:\nginx
nginx -s reload
```

如果只是前端代码更新，通常不需要改 Nginx 配置。

## 十、生产环境建议

- 使用 Nginx 托管 `dist`，不要长期用 `npm run dev`
- API Key 只放在服务器配置或环境变量中，不写入前端源码
- 给服务器配置 HTTPS 证书
- 定期备份 Nginx 配置和项目 `.env.local`
- 若暴露公网，建议限制后台 API 的访问来源或增加额外鉴权
