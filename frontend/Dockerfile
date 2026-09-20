# 前端：构建产物由 nginx 托管，/agent 反向代理到后端（含 SSE 透传）
FROM node:20-alpine AS build

WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build:only

FROM nginx:alpine

COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

# 注意：nginx 默认会缓冲响应，SSE 会被攒成一批再下发。
# nginx.conf 里已对 /agent/ 关闭 buffering 与 gzip。
CMD ["nginx", "-g", "daemon off;"]
