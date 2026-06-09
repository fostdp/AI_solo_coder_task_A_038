"""
数据库写入微服务
订阅Redis频道，批量写入TimescaleDB
"""

from .main import DBWriterService

__all__ = ['DBWriterService']
