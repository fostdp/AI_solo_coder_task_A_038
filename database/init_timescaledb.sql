-- 生物制药冻干机监控系统 TimescaleDB 初始化脚本
-- PostgreSQL 14 + TimescaleDB 2.11

-- 启用TimescaleDB扩展
CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ========== 设备表 ==========
CREATE TABLE IF NOT EXISTS devices (
    id SERIAL PRIMARY KEY,
    name VARCHAR(50) NOT NULL,
    location VARCHAR(100),
    status VARCHAR(20) DEFAULT 'running',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ========== 搁板表 ==========
CREATE TABLE IF NOT EXISTS shelves (
    id SERIAL PRIMARY KEY,
    device_id INTEGER REFERENCES devices(id) ON DELETE CASCADE,
    shelf_number INTEGER NOT NULL,
    temp_sensor_count INTEGER DEFAULT 8,
    vacuum_sensor_count INTEGER DEFAULT 2,
    UNIQUE(device_id, shelf_number)
);

-- ========== 遥测数据表 (超表) ==========
CREATE TABLE IF NOT EXISTS telemetry (
    timestamp TIMESTAMPTZ NOT NULL,
    device_id INTEGER NOT NULL,
    shelf_id INTEGER NOT NULL,
    temp_1 FLOAT, temp_2 FLOAT, temp_3 FLOAT, temp_4 FLOAT,
    temp_5 FLOAT, temp_6 FLOAT, temp_7 FLOAT, temp_8 FLOAT,
    vacuum_1 FLOAT, vacuum_2 FLOAT,
    cold_trap_temp FLOAT,
    power_1 FLOAT, power_2 FLOAT, power_3 FLOAT, power_4 FLOAT,
    power_5 FLOAT, power_6 FLOAT, power_7 FLOAT, power_8 FLOAT,
    PRIMARY KEY (timestamp, device_id, shelf_id),
    FOREIGN KEY (device_id) REFERENCES devices(id) ON DELETE CASCADE,
    FOREIGN KEY (shelf_id) REFERENCES shelves(id) ON DELETE CASCADE
);

-- 创建超表 (仅在表为空时执行)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM timescaledb_information.hypertables 
        WHERE hypertable_name = 'telemetry'
    ) THEN
        PERFORM create_hypertable('telemetry', 'timestamp');
    END IF;
END $$;

-- 创建索引
CREATE INDEX IF NOT EXISTS idx_telemetry_device_time ON telemetry (device_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_telemetry_shelf_time ON telemetry (shelf_id, timestamp DESC);

-- 创建连续聚合视图：每分钟温度统计
CREATE MATERIALIZED VIEW IF NOT EXISTS telemetry_minute
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 minute', timestamp) AS bucket,
    device_id,
    shelf_id,
    AVG((temp_1+temp_2+temp_3+temp_4+temp_5+temp_6+temp_7+temp_8)/8) AS avg_temp,
    MAX(GREATEST(temp_1,temp_2,temp_3,temp_4,temp_5,temp_6,temp_7,temp_8)) AS max_temp,
    MIN(LEAST(temp_1,temp_2,temp_3,temp_4,temp_5,temp_6,temp_7,temp_8)) AS min_temp,
    MAX(GREATEST(temp_1,temp_2,temp_3,temp_4,temp_5,temp_6,temp_7,temp_8)) - 
    MIN(LEAST(temp_1,temp_2,temp_3,temp_4,temp_5,temp_6,temp_7,temp_8)) AS temp_diff,
    AVG((vacuum_1+vacuum_2)/2) AS avg_vacuum,
    AVG(cold_trap_temp) AS avg_cold_trap
FROM telemetry
GROUP BY bucket, device_id, shelf_id
WITH NO DATA;

-- ========== 控制指令表 ==========
CREATE TABLE IF NOT EXISTS control_commands (
    id SERIAL PRIMARY KEY,
    device_id INTEGER REFERENCES devices(id) ON DELETE CASCADE,
    shelf_id INTEGER REFERENCES shelves(id) ON DELETE CASCADE,
    timestamp TIMESTAMPTZ DEFAULT NOW(),
    power_adj_1 FLOAT, power_adj_2 FLOAT, power_adj_3 FLOAT, power_adj_4 FLOAT,
    power_adj_5 FLOAT, power_adj_6 FLOAT, power_adj_7 FLOAT, power_adj_8 FLOAT,
    auto_mode BOOLEAN DEFAULT true
);

CREATE INDEX IF NOT EXISTS idx_control_device_time ON control_commands (device_id, timestamp DESC);

-- ========== 预测结果表 ==========
CREATE TABLE IF NOT EXISTS prediction_results (
    id SERIAL PRIMARY KEY,
    device_id INTEGER REFERENCES devices(id) ON DELETE CASCADE,
    batch_id VARCHAR(50),
    timestamp TIMESTAMPTZ DEFAULT NOW(),
    moisture_pred FLOAT,
    moisture_conf FLOAT,
    moisture_threshold FLOAT DEFAULT 3.0,
    reconstitution_pred FLOAT,
    reconstitution_conf FLOAT,
    reconstitution_threshold FLOAT DEFAULT 120.0,
    drying_rate FLOAT,
    is_qualified BOOLEAN
);

CREATE INDEX IF NOT EXISTS idx_prediction_device_time ON prediction_results (device_id, timestamp DESC);

-- ========== 告警表 ==========
CREATE TABLE IF NOT EXISTS alarms (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    timestamp TIMESTAMPTZ DEFAULT NOW(),
    device_id INTEGER REFERENCES devices(id) ON DELETE CASCADE,
    shelf_id INTEGER REFERENCES shelves(id) ON DELETE CASCADE,
    alarm_type VARCHAR(30) NOT NULL,
    severity VARCHAR(10) NOT NULL,
    message TEXT NOT NULL,
    acknowledged BOOLEAN DEFAULT false,
    acknowledged_by VARCHAR(50),
    acknowledged_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_alarm_device_time ON alarms (device_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_alarm_acknowledged ON alarms (acknowledged) WHERE acknowledged = false;

-- ========== 系统配置表 ==========
CREATE TABLE IF NOT EXISTS system_config (
    key VARCHAR(50) PRIMARY KEY,
    value TEXT,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 插入默认配置
INSERT INTO system_config (key, value) VALUES
('temp_diff_threshold', '1.0'),
('vacuum_min_threshold', '0.1'),
('vacuum_max_threshold', '100.0'),
('cold_trap_max_threshold', '-50.0'),
('moisture_max_threshold', '3.0'),
('reconstitution_max_threshold', '120.0'),
('control_interval', '10'),
('mqtt_broker', 'localhost'),
('mqtt_port', '1883'),
('mqtt_topic', 'pharmacy/mes/alarm'),
('auto_control_enabled', 'true')
ON CONFLICT (key) DO NOTHING;

-- ========== 初始化设备数据 ==========
INSERT INTO devices (name, location) VALUES
('FD-001', '车间A-1号'), ('FD-002', '车间A-2号'), ('FD-003', '车间A-3号'),
('FD-004', '车间B-1号'), ('FD-005', '车间B-2号'), ('FD-006', '车间B-3号'),
('FD-007', '车间C-1号'), ('FD-008', '车间C-2号'), ('FD-009', '车间C-3号'),
('FD-010', '车间D-1号')
ON CONFLICT DO NOTHING;

-- 初始化搁板数据
DO $$
DECLARE
    d_id INTEGER;
    s_num INTEGER;
BEGIN
    FOR d_id IN 1..10 LOOP
        FOR s_num IN 1..5 LOOP
            INSERT INTO shelves (device_id, shelf_number) 
            VALUES (d_id, s_num)
            ON CONFLICT (device_id, shelf_number) DO NOTHING;
        END LOOP;
    END LOOP;
END $$;

-- ========== 查询示例 ==========
-- 查询最新实时数据
-- SELECT * FROM telemetry 
-- WHERE device_id = 1 
-- ORDER BY timestamp DESC 
-- LIMIT 50;

-- 查询温度统计
-- SELECT * FROM telemetry_minute 
-- WHERE device_id = 1 AND bucket > NOW() - INTERVAL '1 hour'
-- ORDER BY bucket DESC;
