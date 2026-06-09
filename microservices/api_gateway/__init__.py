"""
API Gateway微服务
提供FastAPI REST API统一入口，缓存最新数据，支持WebSocket实时推送
"""

from .main import APIGatewayService, app, gateway_service

__all__ = ["APIGatewayService", "app", "gateway_service"]
