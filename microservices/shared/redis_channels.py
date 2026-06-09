"""
Redis Pub/Sub 通道定义
微服务间通信协议
"""

# Redis通道定义
CHANNELS = {
    # 遥测数据通道 - profinet_driver -> 所有订阅者
    'TELEMETRY_RAW': 'telemetry:raw',           # 原始遥测数据
    'TELEMETRY_PROCESSED': 'telemetry:processed',  # 处理后的遥测数据

    # 控制命令通道 - temp_controller -> profinet_driver
    'CONTROL_COMMAND': 'control:command',       # 加热功率调整命令
    'CONTROL_STATUS': 'control:status',         # 控制状态更新

    # 预测结果通道 - quality_predictor -> alarm_publisher / api_gateway
    'PREDICTION_RESULT': 'prediction:result',   # 质量预测结果
    'PREDICTION_CONFIG': 'prediction:config',   # 预测配置更新

    # 告警通道 - alarm_publisher -> api_gateway / mqtt
    'ALARM_EVENT': 'alarm:event',               # 告警事件
    'ALARM_ACK': 'alarm:acknowledge',           # 告警确认
    'ALARM_CONFIG': 'alarm:config',             # 告警配置更新

    # 系统配置通道
    'CONFIG_UPDATE': 'config:update',           # 配置更新通知
    'SYSTEM_STATUS': 'system:status',           # 系统健康状态

    # 数据库写入通道
    'DB_WRITE': 'db:write',                     # 批量写入请求
}

# 消息类型定义
MESSAGE_TYPES = {
    'TELEMETRY': 'telemetry',
    'CONTROL_COMMAND': 'control_command',
    'CONTROL_STATUS': 'control_status',
    'PREDICTION': 'prediction',
    'ALARM': 'alarm',
    'ALARM_ACK': 'alarm_ack',
    'CONFIG': 'config',
    'STATUS': 'status',
    'DB_BATCH': 'db_batch',
}

# 服务ID
SERVICE_IDS = {
    'PROFINET_DRIVER': 'profinet-driver',
    'TEMP_CONTROLLER': 'temp-controller',
    'QUALITY_PREDICTOR': 'quality-predictor',
    'ALARM_PUBLISHER': 'alarm-publisher',
    'API_GATEWAY': 'api-gateway',
    'DB_WRITER': 'db-writer',
}


def get_telemetry_channel(device_id: int) -> str:
    """获取特定设备的遥测通道"""
    return f"{CHANNELS['TELEMETRY_RAW']}:{device_id}"


def get_control_channel(device_id: int) -> str:
    """获取特定设备的控制通道"""
    return f"{CHANNELS['CONTROL_COMMAND']}:{device_id}"


def get_alarm_channel(device_id: int) -> str:
    """获取特定设备的告警通道"""
    return f"{CHANNELS['ALARM_EVENT']}:{device_id}"


def get_prediction_channel(device_id: int) -> str:
    """获取特定设备的预测通道"""
    return f"{CHANNELS['PREDICTION_RESULT']}:{device_id}"
