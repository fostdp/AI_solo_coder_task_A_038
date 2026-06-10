-- ============================================
-- TimescaleDB 优化配置脚本
-- 包含压缩策略、数据保留、连续聚合
-- ============================================

-- 启用必要的扩展
CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;
CREATE EXTENSION IF NOT EXISTS pg_cron CASCADE;

-- ============================================
-- 1. 压缩策略配置
-- ============================================

-- 为telemetry表启用压缩
ALTER TABLE telemetry SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'device_id, shelf_id',
    timescaledb.compress_orderby = 'timestamp DESC'
);

-- 配置压缩策略：30天以上的数据自动压缩
SELECT add_compression_policy(
    hypertable => 'telemetry',
    compress_after => INTERVAL '30 days',
    schedule_interval => INTERVAL '1 day',
    initial_start => TIMESTAMPTZ 'today 02:00:00+08'
);

-- ============================================
-- 2. 连续聚合视图
-- ============================================

-- 1小时聚合视图
CREATE MATERIALIZED VIEW telemetry_hourly
WITH (timescaledb.continuous) AS
SELECT
    time_bucket(INTERVAL '1 hour', timestamp) AS bucket,
    device_id,
    shelf_id,
    COUNT(*) AS sample_count,
    AVG((temp_1+temp_2+temp_3+temp_4+temp_5+temp_6+temp_7+temp_8)/8) AS avg_temperature,
    MIN(LEAST(temp_1,temp_2,temp_3,temp_4,temp_5,temp_6,temp_7,temp_8)) AS min_temperature,
    MAX(GREATEST(temp_1,temp_2,temp_3,temp_4,temp_5,temp_6,temp_7,temp_8)) AS max_temperature,
    MAX(GREATEST(temp_1,temp_2,temp_3,temp_4,temp_5,temp_6,temp_7,temp_8)) - 
    MIN(LEAST(temp_1,temp_2,temp_3,temp_4,temp_5,temp_6,temp_7,temp_8)) AS temp_diff,
    AVG((vacuum_1+vacuum_2)/2) AS avg_vacuum,
    AVG(cold_trap_temp) AS avg_cold_trap_temp,
    AVG((power_1+power_2+power_3+power_4+power_5+power_6+power_7+power_8)/8) AS avg_heating_power
FROM telemetry
GROUP BY bucket, device_id, shelf_id
WITH NO DATA;

-- 启用1小时视图自动刷新
SELECT add_continuous_aggregate_policy(
    continuous_aggregate => 'telemetry_hourly',
    start_offset => INTERVAL '3 hours',
    end_offset => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour'
);

-- 1天聚合视图
CREATE MATERIALIZED VIEW telemetry_daily
WITH (timescaledb.continuous) AS
SELECT
    time_bucket(INTERVAL '1 day', bucket) AS day_bucket,
    device_id,
    shelf_id,
    SUM(sample_count) AS sample_count,
    AVG(avg_temperature) AS avg_temperature,
    MIN(min_temperature) AS min_temperature,
    MAX(max_temperature) AS max_temperature,
    MAX(temp_diff) AS max_temp_diff,
    AVG(avg_vacuum) AS avg_vacuum,
    AVG(avg_cold_trap_temp) AS avg_cold_trap_temp
FROM telemetry_hourly
GROUP BY day_bucket, device_id, shelf_id
WITH NO DATA;

SELECT add_continuous_aggregate_policy(
    continuous_aggregate => 'telemetry_daily',
    start_offset => INTERVAL '3 days',
    end_offset => INTERVAL '1 day',
    schedule_interval => INTERVAL '1 day',
    initial_start => TIMESTAMPTZ 'today 01:00:00+08'
);

-- ============================================
-- 3. 数据保留策略
-- ============================================

-- 原始遥测数据：保留30天
SELECT add_retention_policy(
    hypertable => 'telemetry',
    drop_after => INTERVAL '30 days',
    schedule_interval => INTERVAL '1 day',
    initial_start => TIMESTAMPTZ 'today 03:00:00+08'
);

-- 小时聚合：保留1年
SELECT add_retention_policy(
    hypertable => 'telemetry_hourly',
    drop_after => INTERVAL '1 year',
    schedule_interval => INTERVAL '1 week'
);

-- 天聚合：永久保留（不设置保留策略）

-- 告警数据：保留2年
-- 首先将alarms表转换为超表（如果还不是）
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM timescaledb_information.hypertables 
        WHERE hypertable_name = 'alarms'
    ) THEN
        PERFORM create_hypertable('alarms', 'timestamp');
    END IF;
END $$;

SELECT add_retention_policy(
    hypertable => 'alarms',
    drop_after => INTERVAL '2 years',
    schedule_interval => INTERVAL '1 month'
);

-- 控制命令：保留6个月
-- 首先将control_commands表转换为超表（如果还不是）
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM timescaledb_information.hypertables 
        WHERE hypertable_name = 'control_commands'
    ) THEN
        PERFORM create_hypertable('control_commands', 'timestamp');
    END IF;
END $$;

SELECT add_retention_policy(
    hypertable => 'control_commands',
    drop_after => INTERVAL '6 months',
    schedule_interval => INTERVAL '1 week'
);

-- 预测结果：保留1年
-- 首先将prediction_results表转换为超表（如果还不是）
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM timescaledb_information.hypertables 
        WHERE hypertable_name = 'prediction_results'
    ) THEN
        PERFORM create_hypertable('prediction_results', 'timestamp');
    END IF;
END $$;

SELECT add_retention_policy(
    hypertable => 'prediction_results',
    drop_after => INTERVAL '1 year',
    schedule_interval => INTERVAL '1 week'
);

-- ============================================
-- 4. 索引优化
-- ============================================

CREATE INDEX IF NOT EXISTS idx_telemetry_device_time 
ON telemetry (device_id, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_telemetry_shelf_time 
ON telemetry (device_id, shelf_id, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_alarms_device_time 
ON alarms (device_id, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_alarms_acknowledged 
ON alarms (acknowledged, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_alarms_severity 
ON alarms (severity, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_control_commands_device_time 
ON control_commands (device_id, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_prediction_results_device_time 
ON prediction_results (device_id, timestamp DESC);

-- 聚合视图索引
CREATE INDEX IF NOT EXISTS idx_telemetry_hourly_bucket 
ON telemetry_hourly (bucket DESC, device_id, shelf_id);

CREATE INDEX IF NOT EXISTS idx_telemetry_daily_bucket 
ON telemetry_daily (day_bucket DESC, device_id, shelf_id);

-- ============================================
-- 5. 压缩参数调优
-- ============================================

-- 设置zstd压缩级别为6（平衡压缩率和速度）
SET timescaledb.compress_zstd_level = 6;

-- 启用异步压缩
SET timescaledb.enable_asynchronous_merge = on;

-- ============================================
-- 6. 数据清理日志表
-- ============================================

CREATE TABLE IF NOT EXISTS retention_log (
    id SERIAL PRIMARY KEY,
    table_name TEXT NOT NULL,
    operation TEXT NOT NULL,
    rows_deleted BIGINT,
    execution_time TIMESTAMPTZ DEFAULT NOW(),
    duration_ms INTEGER,
    status TEXT,
    error_message TEXT
);

-- ============================================
-- 7. 告警统计聚合视图
-- ============================================

CREATE MATERIALIZED VIEW alarm_stats_hourly
WITH (timescaledb.continuous) AS
SELECT
    time_bucket(INTERVAL '1 hour', timestamp) AS bucket,
    device_id,
    shelf_id,
    alarm_type,
    severity,
    COUNT(*) AS alarm_count
FROM alarms
GROUP BY bucket, device_id, shelf_id, alarm_type, severity
WITH NO DATA;

SELECT add_continuous_aggregate_policy(
    continuous_aggregate => 'alarm_stats_hourly',
    start_offset => INTERVAL '3 hours',
    end_offset => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour'
);

-- ============================================
-- 8. 手动压缩测试函数
-- ============================================

CREATE OR REPLACE FUNCTION manual_compress(older_than_days INTEGER DEFAULT 30)
RETURNS TABLE (
    hypertable_name TEXT,
    chunks_compressed INTEGER,
    total_chunks INTEGER
) AS $$
DECLARE
    r RECORD;
    compress_count INTEGER := 0;
    total_count INTEGER := 0;
BEGIN
    FOR r IN 
        SELECT show_chunks('telemetry', older_than => (older_than_days || ' days')::INTERVAL) AS chunk
    LOOP
        BEGIN
            PERFORM compress_chunk(r.chunk);
            compress_count := compress_count + 1;
        EXCEPTION WHEN OTHERS THEN
            RAISE NOTICE '压缩 % 失败: %', r.chunk, SQLERRM;
        END;
        total_count := total_count + 1;
    END LOOP;
    
    RETURN QUERY SELECT 'telemetry'::TEXT, compress_count, total_count;
END;
$$ LANGUAGE plpgsql;

-- ============================================
-- 9. 数据清理日志存储过程
-- ============================================

CREATE OR REPLACE FUNCTION log_retention_operation(
    p_table_name TEXT,
    p_operation TEXT,
    p_rows_deleted BIGINT,
    p_duration_ms INTEGER,
    p_status TEXT,
    p_error_message TEXT DEFAULT NULL
) RETURNS VOID AS $$
BEGIN
    INSERT INTO retention_log (
        table_name,
        operation,
        rows_deleted,
        execution_time,
        duration_ms,
        status,
        error_message
    ) VALUES (
        p_table_name,
        p_operation,
        p_rows_deleted,
        NOW(),
        p_duration_ms,
        p_status,
        p_error_message
    );
END;
$$ LANGUAGE plpgsql;

-- ============================================
-- 10. pg_cron 定期清理任务
-- ============================================

-- 每天凌晨2点执行数据清理并记录日志
SELECT cron.schedule(
    job_name => 'daily_retention_cleanup',
    schedule => '0 2 * * *',
    command => $cron_cmd$
    DO $func$
    DECLARE
        v_start_time TIMESTAMPTZ;
        v_end_time TIMESTAMPTZ;
        v_duration_ms INTEGER;
        v_rows_deleted BIGINT;
    BEGIN
        -- 清理telemetry表（30天以上）
        v_start_time := NOW();
        v_rows_deleted := 0;
        
        WITH deleted AS (
            DELETE FROM telemetry 
            WHERE timestamp < NOW() - INTERVAL '30 days'
            RETURNING *
        )
        SELECT COUNT(*) INTO v_rows_deleted FROM deleted;
        
        v_end_time := NOW();
        v_duration_ms := EXTRACT(EPOCH FROM (v_end_time - v_start_time)) * 1000;
        
        PERFORM log_retention_operation(
            'telemetry',
            'retention_cleanup',
            v_rows_deleted,
            v_duration_ms,
            'success'
        );

        -- 清理alarms表（2年以上）
        v_start_time := NOW();
        v_rows_deleted := 0;
        
        WITH deleted AS (
            DELETE FROM alarms 
            WHERE timestamp < NOW() - INTERVAL '2 years'
            RETURNING *
        )
        SELECT COUNT(*) INTO v_rows_deleted FROM deleted;
        
        v_end_time := NOW();
        v_duration_ms := EXTRACT(EPOCH FROM (v_end_time - v_start_time)) * 1000;
        
        PERFORM log_retention_operation(
            'alarms',
            'retention_cleanup',
            v_rows_deleted,
            v_duration_ms,
            'success'
        );

        -- 清理control_commands表（6个月以上）
        v_start_time := NOW();
        v_rows_deleted := 0;
        
        WITH deleted AS (
            DELETE FROM control_commands 
            WHERE timestamp < NOW() - INTERVAL '6 months'
            RETURNING *
        )
        SELECT COUNT(*) INTO v_rows_deleted FROM deleted;
        
        v_end_time := NOW();
        v_duration_ms := EXTRACT(EPOCH FROM (v_end_time - v_start_time)) * 1000;
        
        PERFORM log_retention_operation(
            'control_commands',
            'retention_cleanup',
            v_rows_deleted,
            v_duration_ms,
            'success'
        );

        -- 清理prediction_results表（1年以上）
        v_start_time := NOW();
        v_rows_deleted := 0;
        
        WITH deleted AS (
            DELETE FROM prediction_results 
            WHERE timestamp < NOW() - INTERVAL '1 year'
            RETURNING *
        )
        SELECT COUNT(*) INTO v_rows_deleted FROM deleted;
        
        v_end_time := NOW();
        v_duration_ms := EXTRACT(EPOCH FROM (v_end_time - v_start_time)) * 1000;
        
        PERFORM log_retention_operation(
            'prediction_results',
            'retention_cleanup',
            v_rows_deleted,
            v_duration_ms,
            'success'
        );

    EXCEPTION WHEN OTHERS THEN
        PERFORM log_retention_operation(
            'all_tables',
            'retention_cleanup',
            0,
            0,
            'failed',
            SQLERRM
        );
        RAISE;
    END $func$;
    $cron_cmd$
);

-- ============================================
-- 11. 查询监控函数
-- ============================================

CREATE OR REPLACE FUNCTION get_compression_stats()
RETURNS TABLE (
    hypertable_name TEXT,
    total_chunks BIGINT,
    compressed_chunks BIGINT,
    compression_ratio NUMERIC,
    before_compression_total_size TEXT,
    after_compression_total_size TEXT
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        h.hypertable_name::TEXT,
        h.total_chunks,
        h.compressed_chunks,
        CASE 
            WHEN h.before_compression_total_bytes > 0 
            THEN ROUND((1 - h.after_compression_total_bytes::NUMERIC / h.before_compression_total_bytes) * 100, 2)
            ELSE 0 
        END AS compression_ratio,
        pg_size_pretty(h.before_compression_total_bytes) AS before_compression_total_size,
        pg_size_pretty(h.after_compression_total_bytes) AS after_compression_total_size
    FROM timescaledb_information.hypertables hi
    JOIN LATERAL hypertable_compression_stats(hi.hypertable_name::regclass) h ON true
    WHERE hi.hypertable_name = 'telemetry';
END;
$$ LANGUAGE plpgsql;

-- ============================================
-- 配置完成验证
-- ============================================

-- 查看所有已配置的策略
SELECT 
    job_type,
    hypertable_name,
    schedule_interval,
    next_start,
    status
FROM timescaledb_information.jobs
ORDER BY job_type, hypertable_name;
