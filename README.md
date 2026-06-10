# 生物制药冻干机搁板温度均匀性控制与产品质量预测系统

## 系统架构

### 微服务架构

```
┌─────────────────────────────────────────────────────────────────────────┐
│                              Nginx (8080)                                │
│                           ┌───────────────────┐                          │
│                           │  Gzip压缩 / 反向代理 │                          │
│                           └───────────────────┘                          │
│                                  │                                        │
│              ┌───────────────────┴───────────────────┐                    │
│              │                                       │                    │
│      ┌───────▼────────┐                     ┌────────▼───────┐            │
│      │   Frontend     │                     │  API Gateway   │            │
│      │  (React + Vite)│                     │(FastAPI+Gunicorn)│            │
│      └───────┬────────┘                     └────────┬───────┘            │
│              │                                        │                    │
│              │                                ┌───────▼───────┐            │
│              │                                │  WebSocket    │            │
│              │                                └───────┬───────┘            │
└──────────────┼────────────────────────────────────────┼────────────────────┘
               │                                        │
               │          Redis Pub/Sub (6379)          │
               │    ┌──────────────────────────────┐    │
               │    │  telemetry:raw              │    │
               └────►  control:command            │◄───┘
                    │  control:status             │
                    │  prediction:result          │
                    │  alarm:event                │
                    └──────────────────────────────┘
                           ▲               ▲
                           │               │
              ┌────────────┘               └────────────┐
              │                                         │
  ┌───────────▼───────────┐                 ┌───────────▼───────────┐
  │  profinet-driver      │                 │  temp-controller       │
  │  (数据采集 + 协议解析) │                 │  (模糊控制 + ILC)      │
  └───────────┬───────────┘                 └───────────┬───────────┘
              │                                         │
  ┌───────────▼───────────┐                 ┌───────────▼───────────┐
  │  profinet-simulator   │                 │  quality-predictor     │
  │  (10台设备模拟)        │                 │  (PLS + 迁移学习)      │
  └───────────────────────┘                 └───────────┬───────────┘
                                                         │
  ┌───────────────────────┐                 ┌───────────▼───────────┐
  │  db-writer            │                 │  alarm-publisher       │
  │  (批量写入TimescaleDB)│                 │  (告警检测 + MQTT)     │
  └───────────┬───────────┘                 └───────────┬───────────┘
              │                                         │
  ┌───────────▼───────────┐                 ┌───────────▼───────────┐
  │  TimescaleDB (5432)   │                 │  MQTT Broker (1883)    │
  │  (自动压缩 + 保留策略)│                 │  (EMQX)                │
  └───────────────────────┘                 └───────────────────────┘
```

### 服务列表

| 服务 | 端口 | 说明 |
|------|------|------|
| nginx | 8080 | 反向代理 + Gzip压缩 |
| frontend | - | React前端，由Nginx提供 |
| api-gateway | 8000 | FastAPI + Gunicorn + Uvicorn Worker |
| timescale-db | 5432 | 时序数据库，自动压缩和保留 |
| redis | 6379 | 消息队列和缓存 |
| mqtt-broker | 1883 | EMQX MQTT Broker |
| profinet-simulator | - | Profinet设备模拟器 |
| profinet-driver | - | 数据采集和协议解析 |
| temp-controller | - | 温度均匀性控制 |
| quality-predictor | - | 产品质量预测 |
| alarm-publisher | - | 告警检测和MQTT推送 |
| db-writer | - | 批量写入数据库 |

---

## 快速部署

### 环境要求

- Docker 24.0+
- Docker Compose v2.20+
- 4核CPU，8GB内存以上

### 一键部署

```bash
# 1. 克隆项目
git clone <repository-url>
cd AI_solo_coder_task_A_038

# 2. 复制环境变量配置
cp .env.example .env

# 3. 修改密码等敏感配置（可选）
# 编辑 .env 文件，修改 POSTGRES_PASSWORD 等

# 4. 构建并启动所有服务
docker-compose up -d --build

# 5. 查看服务状态
docker-compose ps

# 6. 查看日志
docker-compose logs -f api-gateway
```

### 访问地址

| 服务 | 地址 |
|------|------|
| 系统首页 | http://localhost:8080 |
| API文档 | http://localhost:8080/docs |
| EMQX控制台 | http://localhost:18083 |
| MQTT端口 | localhost:1883 |

### 停止服务

```bash
# 停止但保留数据
docker-compose down

# 停止并删除所有数据（谨慎使用）
docker-compose down -v
```

---

## 部署架构说明

### 1. API Gateway (FastAPI + Gunicorn + Uvicorn)

**架构模式**：Gunicorn作为master进程，管理多个Uvicorn Worker进程

```
                          ┌─────────────────┐
                          │   Gunicorn      │
                          │   (Master)      │
                          └────────┬────────┘
                                   │
          ┌────────────────┬───────┴───────┬────────────────┐
          │                │               │                │
    ┌─────▼─────┐    ┌─────▼─────┐   ┌─────▼─────┐   ┌─────▼─────┐
    │  Uvicorn  │    │  Uvicorn  │   │  Uvicorn  │   │  Uvicorn  │
    │  Worker 1 │    │  Worker 2 │   │  Worker 3 │   │  Worker 4 │
    └─────┬─────┘    └─────┬─────┘   └─────┬─────┘   └─────┬─────┘
          │                │               │                │
          └────────────────┴───────────────┴────────────────┘
                                   │
                          ┌────────▼────────┐
                          │   FastAPI App   │
                          └─────────────────┘
```

**配置说明**（[gunicorn.conf.py](docker/gunicorn.conf.py)）：
- Worker数：CPU核数 * 2 + 1（默认4）
- Worker类型：uvicorn.workers.UvicornWorker
- 请求超时：120秒
- 最大请求数：1000（防内存泄漏）
- 预加载应用：True

**环境变量**：
```env
GUNICORN_WORKERS=4
GUNICORN_TIMEOUT=120
PORT=8000
```

### 2. TimescaleDB 自动压缩和保留策略

**压缩策略**（[timescale_config.sql](database/timescale_config.sql#L14-L27)）：
- 压缩算法：zstd（级别6）
- 压缩分段：按 device_id, shelf_id
- 自动压缩：30天以上数据
- 压缩调度：每天凌晨2:00

**数据保留**：
| 数据类型 | 保留期限 |
|---------|----------|
| 原始遥测 | 30天 |
| 小时聚合 | 1年 |
| 天聚合 | 永久 |
| 告警数据 | 2年 |
| 控制命令 | 6个月 |
| 预测结果 | 1年 |

**连续聚合**：
- `telemetry_hourly`：1小时聚合，每小时自动刷新
- `telemetry_daily`：1天聚合，每天自动刷新

### 3. 前端 Gzip 压缩

**Nginx配置**（[nginx.conf](docker/nginx.conf)）：
```nginx
gzip on;
gzip_vary on;
gzip_proxied any;
gzip_comp_level 6;
gzip_min_length 1024;
gzip_types
    text/plain
    text/css
    text/xml
    text/javascript
    application/javascript
    application/json
    application/xml
    image/svg+xml
    font/ttf
    font/otf;
```

**缓存策略**：
- 带hash的静态资源（*.js, *.css）：缓存1年，immutable
- 图片资源：缓存30天
- index.html：不缓存

---

## Profinet 模拟器使用说明

### 模拟器配置

支持通过环境变量配置模拟器参数（`.env`）：

```env
# 设备配置
SIM_NUM_DEVICES=10              # 冻干机数量
SIM_NUM_SHELVES=5               # 每台搁板数
SIM_TEMP_SENSORS_PER_SHELF=8    # 每层温度传感器数
SIM_VACUUM_SENSORS_PER_SHELF=2  # 每层真空度传感器数
SIM_REPORT_INTERVAL=10          # 数据上报间隔（秒）

# 异常注入配置
SIM_ENABLE_ANOMALY=true         # 启用自动异常注入
SIM_ANOMALY_INTERVAL=120        # 自动异常间隔（秒）
```

### 交互式命令

连接到模拟器容器进行交互：

```bash
# 连接到模拟器
docker attach freeze-dryer-simulator

# 或使用exec
docker exec -it freeze-dryer-simulator bash
```

#### 异常注入命令

```bash
# 查看帮助
help

# 查看状态
status

# 注入温度尖峰（指定设备1，搁板2，传感器3，持续60秒，强度2.0）
anomaly inject temp_spike --device 1 --shelf 2 --sensor 3 --duration 60 --strength 2.0

# 注入温度偏移（影响设备1所有搁板）
anomaly inject temp_offset --device 1 --strength 1.5

# 注入真空度尖峰
anomaly inject vacuum_spike --device 1 --shelf 1 --duration 30

# 注入传感器故障
anomaly inject sensor_failure --device 1 --shelf 3 --sensor 5

# 注入冷阱温度过高
anomaly inject cold_trap_high --duration 120

# 查看活跃异常
anomaly list

# 清除指定异常
anomaly clear <anomaly_id>

# 清除所有异常
anomaly clear all

# 开关自动异常注入
anomaly auto on
anomaly auto off

# 开始新批次
batch new 1
```

#### 异常类型说明

| 异常类型 | 说明 | 影响 |
|---------|------|------|
| `temp_spike` | 温度尖峰 | 瞬间±5℃ × 强度 |
| `temp_offset` | 温度偏移 | 持续偏高/偏低3℃ × 强度 |
| `vacuum_spike` | 真空度尖峰 | 突升10Pa × 强度 |
| `vacuum_drift` | 真空度漂移 | 持续恶化，随时间累积 |
| `sensor_failure` | 传感器故障 | 输出固定值或NaN |
| `cold_trap_high` | 冷阱温度过高 | 温度升至-40℃以上 |
| `random_noise` | 随机噪声放大 | 噪声幅度增大3倍 |

#### 命令参数说明

```
anomaly inject <type> [选项]

选项：
  --device N        指定设备（1-10，默认随机）
  --shelf N         指定搁板（1-5，默认所有）
  --sensor N        指定传感器（1-8，默认所有）
  --duration S      持续秒数（默认60）
  --strength F      强度系数（默认1.0，0.1-5.0）
```

### 异常数据示例

模拟器返回的数据中包含 `anomalies` 字段：

```json
{
  "device_id": 1,
  "shelf_id": 1,
  "temperatures": [-45.2, -44.8, -38.5, -45.0, -45.3, -44.9, -45.1, -44.7],
  "vacuum_levels": [1.05, 1.03],
  "cold_trap_temp": -35.2,
  "anomalies": {
    "applied": [
      {
        "id": "anom_abc123",
        "type": "temp_offset",
        "device_id": 1,
        "shelf_id": 1,
        "sensor_id": 3,
        "strength": 1.5,
        "effect": "+6.5℃"
      }
    ],
    "active": [
      {
        "id": "anom_abc123",
        "type": "temp_offset",
        "remaining": 45,
        "target": "Device 1, Shelf 1, Sensor 3"
      }
    ]
  }
}
```

---

## 配置说明

### 配置文件

所有配置文件位于 `config/` 目录，支持热更新：

| 文件 | 说明 |
|------|------|
| [control_params.yaml](config/control_params.yaml) | 控制参数：模糊控制规则、ILC参数、功率分配 |
| [model_params.yaml](config/model_params.yaml) | 模型参数：PLS配置、迁移学习、配方库 |
| [alarm_thresholds.yaml](config/alarm_thresholds.yaml) | 告警阈值：温度、真空度、冷阱、质量阈值 |

### 环境变量

完整的环境变量配置见 [.env.example](.env.example)，主要分组：

1. **数据库配置** - PostgreSQL/TimescaleDB连接
2. **Redis配置** - 消息队列连接
3. **MQTT配置** - MQTT Broker连接
4. **模拟器配置** - 设备数量、传感器、异常注入
5. **API网关配置** - Gunicorn worker、超时
6. **微服务配置** - 控制/预测/告警间隔
7. **前端配置** - API基础路径
8. **日志配置** - 日志级别

---

## 目录结构

```
AI_solo_coder_task_A_038/
├── backend/                    # 单体后端（保留，已重构为微服务）
│   ├── app/
│   └── profinet_simulator.py   # Profinet模拟器（增强版）
├── frontend/                   # React前端
│   └── src/
│       └── components/
│           ├── shelf_thermal.ts      # 热力图独立模块
│           ├── quality_dashboard.ts  # 质量仪表盘独立模块
│           ├── Heatmap.tsx           # 重构后使用shelf_thermal
│           └── QualityPrediction.tsx # 重构后使用quality_dashboard
├── microservices/              # 微服务
│   ├── shared/                 # 共享模块
│   │   ├── redis_channels.py       # Redis通道定义
│   │   ├── message_protocol.py     # 消息协议
│   │   ├── config_loader.py        # 配置加载器
│   │   └── redis_client.py         # Redis客户端基类
│   ├── profinet_driver/        # 数据采集微服务
│   ├── temp_controller/        # 温度控制微服务
│   ├── quality_predictor/      # 质量预测微服务
│   ├── alarm_publisher/        # 告警发布微服务
│   ├── api_gateway/            # API网关
│   ├── db_writer/              # 数据库写入
│   ├── run_regression_tests.py # 回归测试脚本
│   └── start_all_services.ps1  # 本地启动脚本
├── config/                     # 外置配置
│   ├── control_params.yaml
│   ├── model_params.yaml
│   └── alarm_thresholds.yaml
├── database/                   # 数据库脚本
│   ├── init_timescaledb.sql    # 初始化脚本
│   └── timescale_config.sql    # 压缩和保留策略
├── docker/                     # Docker配置
│   ├── Dockerfile.microservice
│   ├── Dockerfile.api-gateway
│   ├── Dockerfile.simulator
│   ├── Dockerfile.frontend
│   ├── nginx.conf              # Nginx配置（含Gzip）
│   ├── gunicorn.conf.py        # Gunicorn配置
│   └── docker-entrypoint.sh    # 前端启动脚本
├── docker-compose.yml          # Docker Compose编排
├── .env.example                # 环境变量示例
└── README.md                   # 本文档
```

---

## 常见问题

### 1. 服务启动失败

```bash
# 查看详细日志
docker-compose logs <service-name>

# 常见原因：
# - 端口被占用：修改 .env 中的端口配置
# - 内存不足：调整 GUNICORN_WORKERS 或关闭其他服务
# - 数据库初始化失败：删除volume后重启
```

### 2. 模拟器无法注入异常

确保容器以交互模式运行：

```bash
# 检查docker-compose.yml中是否配置了stdin_open和tty
# 重新附加到容器
docker attach freeze-dryer-simulator
```

### 3. 前端无法连接API

检查API_BASE_URL配置：

```bash
# 构建时指定
docker build --build-arg VITE_API_BASE_URL=http://your-api:8000 -t frontend .

# 运行时指定
docker run -e API_BASE_URL=http://your-api:8000 frontend
```

### 4. 数据没有被压缩

检查压缩策略：

```sql
-- 查看压缩策略
SELECT * FROM timescaledb_information.compression_settings;

-- 查看已压缩chunk
SELECT * FROM timescaledb_information.compressed_chunk_stats;

-- 手动触发压缩
SELECT manual_compress(30);
```

### 5. 回归测试

运行回归测试验证所有微服务：

```bash
cd microservices
python run_regression_tests.py
```

---

## 监控和运维

### 查看服务状态

```bash
# 查看所有服务状态
docker-compose ps

# 查看资源使用
docker stats

# 查看日志（支持grep过滤）
docker-compose logs -f api-gateway | grep ERROR
```

### 数据备份

```bash
# 备份数据库
docker exec freeze-dryer-timescaledb pg_dump -U postgres freeze_dryer > backup_$(date +%Y%m%d).sql

# 备份Redis
docker exec freeze-dryer-redis redis-cli --rdb /data/dump.rdb
docker cp freeze-dryer-redis:/data/dump.rdb ./backup/
```

### 日志管理

日志默认滚动策略：单个文件10MB，保留3个文件。

```bash
# 清理旧日志
docker system prune -af

# 查看日志大小
du -sh /var/lib/docker/containers/*/*-json.log
```

---

## 技术栈

### 后端
- Python 3.11
- FastAPI 0.104+
- Gunicorn 21.2 + Uvicorn 0.24
- SQLAlchemy 2.0 + asyncpg
- Redis (redis-py)
- Paho-MQTT
- scikit-learn 1.3+
- NumPy, Pandas

### 前端
- React 18 + TypeScript
- Vite 5
- Zustand (状态管理)
- ECharts 5 (图表)
- Tailwind CSS 3
- Canvas 2D

### 基础设施
- TimescaleDB 2.13 (PostgreSQL 16)
- Redis 7.2
- EMQX 5.3 (MQTT Broker)
- Nginx 1.25
- Docker + Docker Compose

---

## License

MIT License
