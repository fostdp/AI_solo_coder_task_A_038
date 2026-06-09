# 生物制药冻干机搁板温度均匀性控制与产品质量预测系统

## 系统概述

本系统是一套完整的生物制药冻干机监控解决方案，实现了10台冻干机、50层搁板的实时温度监控、均匀性控制和产品质量预测。

## 系统架构

```
┌─────────────────┐     Profinet     ┌─────────────────┐     MQTT      ┌─────────────┐
│ Profinet 模拟器 │ ───────────────> │  FastAPI 后端   │ ───────────> │   MES系统   │
└─────────────────┘                  └─────────────────┘               └─────────────┘
                                             │
                                             │ HTTP/WebSocket
                                             ▼
                                  ┌─────────────────┐
                                  │   React 前端     │
                                  │  - Canvas热力图  │
                                  │  - ECharts曲线  │
                                  └─────────────────┘
                                             │
                                             ▼
                                  ┌─────────────────┐
                                  │  TimescaleDB    │
                                  │  (PostgreSQL)   │
                                  └─────────────────┘
```

## 核心功能

### 1. 数据采集
- 10台冻干机，每台5层搁板
- 每层8个温度传感器 + 2个真空度传感器
- 每10秒通过Profinet协议上报数据
- 冷阱温度、加热功率同步采集

### 2. 温度均匀性控制
- **模糊控制算法**：35条模糊规则，7×5输入空间
  - 输入：温度误差、温度变化率
  - 输出：功率调整量
  - 去模糊化：重心法
- **迭代学习控制**：基于批次数据迭代优化
- **控制目标**：搁板间温差 < 1℃

### 3. 产品质量预测
- **偏最小二乘回归(PLS)**：11个特征，6个主成分
- 预测指标：
  - 水分含量（阈值：< 3.0%）
  - 复溶时间（阈值：< 5.0min）
- 置信度评估
- 不合格预警

### 4. 告警系统
- 温差超限（> 1.0℃）
- 真空度异常（< 0.0001Pa 或 > 0.1Pa）
- 冷阱温度过高（> -50℃）
- 质量预测不合格
- MQTT推送至MES系统

## 技术栈

### 后端
- **框架**: FastAPI 0.104+
- **数据库**: TimescaleDB (PostgreSQL扩展)
- **ORM**: SQLAlchemy 2.0 (异步)
- **驱动**: asyncpg
- **机器学习**: scikit-learn, numpy, scipy
- **消息队列**: paho-mqtt
- **算法**: 模糊控制、迭代学习控制、PLS回归

### 前端
- **框架**: React 18 + TypeScript
- **构建工具**: Vite 5
- **样式**: Tailwind CSS 3
- **状态管理**: Zustand
- **图表**: ECharts 5
- **绘图**: Canvas 2D API
- **图标**: Lucide React
- **HTTP**: Axios

## 项目结构

```
AI_solo_coder_task_A_038/
├── database/
│   └── init_timescaledb.sql          # 数据库初始化脚本
├── backend/
│   ├── app/
│   │   ├── main.py                   # FastAPI主应用
│   │   ├── core/
│   │   │   ├── config.py             # 配置管理
│   │   │   └── database.py           # 数据库连接
│   │   ├── models/
│   │   │   └── models.py             # SQLAlchemy ORM模型
│   │   ├── schemas/
│   │   │   └── telemetry.py          # Pydantic数据模型
│   │   ├── services/
│   │   │   ├── control.py            # 温度控制服务
│   │   │   ├── prediction.py         # 质量预测服务
│   │   │   ├── alarm.py              # 告警检测服务
│   │   │   └── mqtt.py               # MQTT发布服务
│   │   └── api/
│   │       ├── devices.py            # 设备API
│   │       ├── data.py               # 数据API
│   │       ├── control.py            # 控制API
│   │       ├── prediction.py         # 预测API
│   │       └── alarm.py              # 告警API
│   ├── profinet_simulator.py         # Profinet模拟器
│   ├── requirements.txt              # Python依赖
│   └── .env                          # 环境变量
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   │   ├── Heatmap.tsx           # Canvas温度热力图
│   │   │   ├── VacuumChart.tsx       # 真空度ECharts曲线
│   │   │   ├── DeviceOverview.tsx    # 设备概览
│   │   │   ├── ControlPanel.tsx      # 功率控制面板
│   │   │   ├── QualityPrediction.tsx # 质量预测面板
│   │   │   └── AlarmPanel.tsx        # 告警面板
│   │   ├── services/
│   │   │   └── api.ts                # API服务
│   │   ├── store/
│   │   │   └── index.ts              # Zustand状态管理
│   │   ├── types/
│   │   │   └── index.ts              # TypeScript类型定义
│   │   ├── App.tsx                   # 主应用组件
│   │   ├── main.tsx                  # 入口文件
│   │   └── index.css                 # 全局样式
│   ├── package.json                  # NPM依赖
│   ├── vite.config.ts                # Vite配置
│   ├── tailwind.config.js            # Tailwind配置
│   ├── tsconfig.json                 # TypeScript配置
│   └── .env                          # 环境变量
├── start_backend.bat                 # 后端启动脚本(Windows)
├── start_frontend.bat                # 前端启动脚本(Windows)
├── start_all.bat                     # 一键启动脚本(Windows)
└── README.md                         # 项目说明
```

## 快速开始

### 前置要求
- Python 3.10+
- Node.js 18+
- PostgreSQL 13+ with TimescaleDB extension
- MQTT Broker (如 Mosquitto)

### 1. 数据库初始化

```sql
-- 创建数据库
CREATE DATABASE freeze_dryer_db;

-- 连接到数据库
\c freeze_dryer_db

-- 创建TimescaleDB扩展
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- 执行初始化脚本
\i database/init_timescaledb.sql
```

### 2. 后端启动

```bash
cd backend
pip install -r requirements.txt
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### 3. 前端启动

```bash
cd frontend
npm install
npm run dev
```

### 4. Profinet模拟器启动

```bash
cd backend
python profinet_simulator.py
```

### 5. 一键启动（Windows）

```bash
# 启动所有服务
start_all.bat
```

## API文档

启动后端后访问：
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

### 主要API端点

#### 设备管理
- `GET /api/devices` - 获取所有设备
- `GET /api/devices/{id}` - 获取设备详情
- `GET /api/devices/{id}/shelves` - 获取设备搁板列表

#### 数据采集
- `POST /api/data/telemetry` - 上报遥测数据
- `GET /api/data/realtime/{device_id}` - 获取实时数据
- `GET /api/data/history` - 获取历史数据
- `GET /api/data/stats/{device_id}` - 获取统计数据

#### 功率控制
- `POST /api/control/power` - 发送功率控制指令
- `PUT /api/control/mode` - 设置控制模式（自动/手动）
- `GET /api/control/calculate/{device_id}/{shelf_id}` - 计算功率调整量
- `GET /api/control/status/{device_id}` - 获取控制状态

#### 质量预测
- `POST /api/prediction/quality` - 执行质量预测
- `GET /api/prediction/result/{device_id}` - 获取预测结果
- `GET /api/prediction/model/{device_id}` - 获取模型信息

#### 告警管理
- `GET /api/alarm/current` - 获取当前告警
- `GET /api/alarm/history` - 获取历史告警
- `POST /api/alarm/acknowledge` - 确认告警
- `POST /api/alarm/check` - 手动检测告警
- `GET /api/alarm/mqtt/status` - 获取MQTT连接状态

## 前端功能

### 温度热力图
- Canvas绘制8个传感器温度分布
- 颜色渐变：蓝→绿→黄→红
- 温度不均匀区域（温差>1℃）用红色虚线框标注
- 鼠标悬停显示精确温度值
- 实时显示温差和均匀性状态

### 真空度曲线
- ECharts动态折线图
- 5层搁板数据同时展示
- 支持缩放、平移
- 实时数据更新（5秒间隔）
- 数据滑块浏览历史

### 功率控制面板
- 自动/手动模式切换
- 8个加热丝独立功率调整（滑块）
- 智能计算按钮（调用后端模糊控制算法）
- 阈值设置

### 质量预测面板
- SVG仪表盘展示水分含量和复溶时间
- 置信度进度条
- 历史预测记录
- 阈值设置
- 不合格预警

### 告警面板
- 实时告警列表
- 按状态筛选（全部/未处理/已确认）
- 严重级别标识
- 一键确认功能
- 告警详情展示

## 算法说明

### 模糊控制算法

**输入变量**：
- 温度误差 e = T_set - T_current，范围：[-5, 5]℃
- 温度变化率 Δe，范围：[-0.5, 0.5]℃/s

**输出变量**：
- 功率调整量 ΔP，范围：[-10, 10]%

**模糊集合**：
- 温度误差：NB, NM, NS, ZE, PS, PM, PB (7个)
- 温度变化率：NB, NS, ZE, PS, PB (5个)
- 输出：NB, NM, NS, ZE, PS, PM, PB (7个)

**模糊规则**：共35条，示例：
```
IF e = NB AND Δe = PB THEN ΔP = PB
IF e = NB AND Δe = PS THEN ΔP = PB
IF e = NM AND Δe = ZE THEN ΔP = PM
...
```

**去模糊化**：重心法

### 迭代学习控制

**控制律**：
```
u_{k+1}(t) = u_k(t) + Γ * e_k(t) + Φ * Δe_k(t)
```
- u_k(t): 第k批次t时刻的控制输入
- e_k(t): 第k批次的跟踪误差
- Γ, Φ: 学习增益矩阵

### PLS回归预测

**特征变量**（11个）：
1. 平均温度
2. 温度标准差
3. 最大温差
4. 平均真空度
5. 真空度标准差
6. 平均加热功率
7. 冷阱温度
8. 干燥速率
9. 温度变化率
10. 真空度变化率
11. 累计加热能量

**主成分数**：6个

**预测输出**：
- 水分含量 (%)
- 复溶时间 (min)

## 数据库设计

### 超表 (Hypertable)

**telemetry** - 遥测数据表
- timestamp (TIMESTAMPTZ)
- device_id (INT)
- shelf_id (INT)
- temperatures (FLOAT8[8])
- vacuum_levels (FLOAT8[2])
- cold_trap_temp (FLOAT8)
- heating_powers (FLOAT8[8])

### 连续聚合视图

**telemetry_minute** - 分钟级聚合
- 每分钟的平均温度、平均真空度、平均功率
- 自动实时聚合

### 其他表

- **devices** - 设备信息表
- **shelves** - 搁板信息表
- **control_commands** - 控制指令表
- **prediction_results** - 预测结果表
- **alarms** - 告警表
- **system_config** - 系统配置表

## 告警配置

| 告警类型 | 阈值 | 严重级别 | 冷却时间 |
|---------|------|---------|---------|
| 温差超限 | > 1.0℃ | Warning | 30s |
| 真空度异常 | < 0.0001Pa 或 > 0.1Pa | Warning | 30s |
| 冷阱温度过高 | > -50℃ | Critical | 30s |
| 质量预警 | 水分>3.0% 或 复溶>5min | Warning | 60s |

## MQTT告警消息格式

```json
{
  "alarm_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "timestamp": "2024-01-15T10:30:00Z",
  "device_id": 1,
  "shelf_id": 3,
  "alarm_type": "temperature_diff",
  "severity": "critical",
  "message": "搁板3温差1.5℃，超过阈值1.0℃",
  "metrics": {
    "temperature_diff": 1.5,
    "threshold": 1.0,
    "temperatures": [-52.1, -51.8, -50.5, -52.0, -51.9, -51.7, -52.2, -51.9]
  }
}
```

## 性能指标

- 数据采集延迟：< 1s
- 控制计算时间：< 100ms
- 预测计算时间：< 500ms
- 前端渲染帧率：60fps
- 支持并发连接：> 1000

## 开发说明

### 添加新的控制算法

在 `backend/app/services/control.py` 中继承 `BaseController` 类：

```python
class MyController(BaseController):
    def calculate(self, current_temp: float, avg_temp: float = None) -> float:
        # 实现你的控制算法
        pass
```

### 添加新的预测模型

在 `backend/app/services/prediction.py` 中扩展 `PLSPredictor` 或添加新的预测器类。

### 前端组件开发

组件位于 `frontend/src/components/`，使用 TypeScript + React Hooks。

## 故障排查

### 后端无法启动
1. 检查数据库连接配置
2. 确认TimescaleDB扩展已安装
3. 检查端口8000是否被占用

### 前端无法连接后端
1. 检查后端服务是否启动
2. 确认CORS配置正确
3. 检查 `.env` 中的 `VITE_API_URL`

### MQTT连接失败
1. 确认MQTT Broker已启动
2. 检查连接配置（主机、端口、用户名、密码）
3. 查看后端日志获取详细错误信息

## License

MIT License

## 联系方式

如有问题，请提交Issue或联系技术支持团队。
