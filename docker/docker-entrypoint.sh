#!/bin/sh
# 替换环境变量
find /usr/share/nginx/html -name "*.js" -exec sed -i "s|__API_BASE_URL__|${API_BASE_URL:-/api}|g" {} \;
exec nginx -g "daemon off;"
