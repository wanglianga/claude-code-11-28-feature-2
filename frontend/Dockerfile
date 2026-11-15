# 多阶段构建：Node 编译静态资源，Nginx 托管并反向代理 API
FROM node:22-alpine AS build
WORKDIR /app
ENV npm_config_registry=https://registry.npmmirror.com
COPY package.json package-lock.json* ./
RUN npm install
COPY . .
RUN npm run build

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
