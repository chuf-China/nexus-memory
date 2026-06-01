#!/usr/bin/env python3
"""observability.py — 可观测性模块

功能:
1. 结构化日志 (JSON 格式)
2. 指标收集 (Prometheus 格式)
3. 健康检查
4. 追踪 (OpenTelemetry 格式)
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# ============ 结构化日志 ============

class StructuredLogger:
    """结构化日志器"""
    
    def __init__(
        self,
        name: str,
        level: str = "INFO",
        log_file: str = None,
        json_format: bool = True,
    ):
        self.logger = logging.getLogger(name)
        self.logger.setLevel(getattr(logging, level.upper()))
        self.json_format = json_format
        
        # 控制台处理器
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.DEBUG)
        
        if json_format:
            console_handler.setFormatter(JsonFormatter())
        else:
            console_handler.setFormatter(
                logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
            )
        
        self.logger.addHandler(console_handler)
        
        # 文件处理器
        if log_file:
            os.makedirs(os.path.dirname(log_file), exist_ok=True)
            file_handler = logging.FileHandler(log_file)
            file_handler.setLevel(logging.DEBUG)
            file_handler.setFormatter(JsonFormatter())
            self.logger.addHandler(file_handler)
    
    def _log(self, level: str, message: str, **kwargs):
        """记录日志"""
        extra = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": level,
            "message": message,
            **kwargs,
        }
        
        getattr(self.logger, level.lower())(
            message,
            extra={"structured": extra} if not self.json_format else None,
        )
        
        if self.json_format:
            # 直接输出 JSON
            print(json.dumps(extra), file=sys.stdout)
    
    def info(self, message: str, **kwargs):
        self._log("INFO", message, **kwargs)
    
    def warning(self, message: str, **kwargs):
        self._log("WARNING", message, **kwargs)
    
    def error(self, message: str, **kwargs):
        self._log("ERROR", message, **kwargs)
    
    def debug(self, message: str, **kwargs):
        self._log("DEBUG", message, **kwargs)
    
    def critical(self, message: str, **kwargs):
        self._log("CRITICAL", message, **kwargs)


class JsonFormatter(logging.Formatter):
    """JSON 格式化器"""
    
    def format(self, record):
        log_data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        
        if hasattr(record, "structured"):
            log_data.update(record.structured)
        
        return json.dumps(log_data)


# ============ 指标收集 ============

class MetricsCollector:
    """指标收集器"""
    
    def __init__(self):
        self.counters: Dict[str, int] = {}
        self.gauges: Dict[str, float] = {}
        self.histograms: Dict[str, List[float]] = {}
        self.start_time = time.time()
    
    def increment(self, name: str, value: int = 1):
        """增加计数器"""
        self.counters[name] = self.counters.get(name, 0) + value
    
    def set_gauge(self, name: str, value: float):
        """设置仪表盘值"""
        self.gauges[name] = value
    
    def observe(self, name: str, value: float):
        """记录直方图值"""
        if name not in self.histograms:
            self.histograms[name] = []
        self.histograms[name].append(value)
    
    @contextmanager
    def timer(self, name: str):
        """计时器上下文管理器"""
        start = time.time()
        yield
        duration = time.time() - start
        self.observe(name, duration)
    
    def get_metrics(self) -> Dict:
        """获取所有指标"""
        metrics = {
            "counters": self.counters,
            "gauges": self.gauges,
            "histograms": {},
            "uptime_seconds": time.time() - self.start_time,
        }
        
        # 计算直方图统计
        for name, values in self.histograms.items():
            if values:
                sorted_values = sorted(values)
                metrics["histograms"][name] = {
                    "count": len(values),
                    "sum": sum(values),
                    "min": min(values),
                    "max": max(values),
                    "mean": sum(values) / len(values),
                    "p50": sorted_values[len(sorted_values) // 2],
                    "p95": sorted_values[int(len(sorted_values) * 0.95)],
                    "p99": sorted_values[int(len(sorted_values) * 0.99)],
                }
        
        return metrics
    
    def export_prometheus(self) -> str:
        """导出 Prometheus 格式"""
        lines = []
        
        # 计数器
        for name, value in self.counters.items():
            lines.append(f"# TYPE {name} counter")
            lines.append(f"{name} {value}")
        
        # 仪表盘
        for name, value in self.gauges.items():
            lines.append(f"# TYPE {name} gauge")
            lines.append(f"{name} {value}")
        
        # 直方图
        for name, values in self.histograms.items():
            if values:
                sorted_values = sorted(values)
                lines.append(f"# TYPE {name} histogram")
                lines.append(f"{name}_count {len(values)}")
                lines.append(f"{name}_sum {sum(values)}")
                
                # 分位数
                for bucket in [0.1, 0.5, 1.0, 5.0, 10.0]:
                    count = sum(1 for v in values if v <= bucket)
                    lines.append(f'{name}_bucket{{le="{bucket}"}} {count}')
        
        return "\n".join(lines)


# ============ 健康检查 ============

class HealthChecker:
    """健康检查器"""
    
    def __init__(self):
        self.checks: Dict[str, callable] = {}
    
    def register(self, name: str, check_fn: callable):
        """注册健康检查"""
        self.checks[name] = check_fn
    
    def run_checks(self) -> Dict:
        """运行所有健康检查"""
        results = {}
        all_healthy = True
        
        for name, check_fn in self.checks.items():
            try:
                result = check_fn()
                results[name] = {
                    "status": "healthy" if result else "unhealthy",
                    "details": result if isinstance(result, dict) else None,
                }
                if not result:
                    all_healthy = False
            except Exception as e:
                results[name] = {
                    "status": "unhealthy",
                    "error": str(e),
                }
                all_healthy = False
        
        return {
            "status": "healthy" if all_healthy else "unhealthy",
            "checks": results,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


# ============ 追踪 ============

class Tracer:
    """追踪器"""
    
    def __init__(self, service_name: str = "nexus-memory"):
        self.service_name = service_name
        self.spans: List[Dict] = []
    
    @contextmanager
    def start_span(self, name: str, attributes: Dict = None):
        """开始一个新的 span"""
        span = {
            "trace_id": os.urandom(16).hex(),
            "span_id": os.urandom(8).hex(),
            "name": name,
            "service": self.service_name,
            "start_time": time.time(),
            "attributes": attributes or {},
        }
        
        try:
            yield span
        except Exception as e:
            span["error"] = str(e)
            raise
        finally:
            span["end_time"] = time.time()
            span["duration"] = span["end_time"] - span["start_time"]
            self.spans.append(span)
    
    def get_spans(self) -> List[Dict]:
        """获取所有 spans"""
        return self.spans
    
    def export_json(self) -> str:
        """导出 JSON 格式"""
        return json.dumps({
            "service": self.service_name,
            "spans": self.spans,
        }, indent=2)


# ============ 全局实例 ============

# 默认日志器
logger = StructuredLogger("nexus", level="INFO")

# 默认指标收集器
metrics = MetricsCollector()

# 默认健康检查器
health = HealthChecker()

# 默认追踪器
tracer = Tracer()


# ============ 便捷函数 ============

def log_info(message: str, **kwargs):
    logger.info(message, **kwargs)

def log_warning(message: str, **kwargs):
    logger.warning(message, **kwargs)

def log_error(message: str, **kwargs):
    logger.error(message, **kwargs)

def log_debug(message: str, **kwargs):
    logger.debug(message, **kwargs)

def increment_counter(name: str, value: int = 1):
    metrics.increment(name, value)

def set_gauge(name: str, value: float):
    metrics.set_gauge(name, value)

def observe_histogram(name: str, value: float):
    metrics.observe(name, value)

def timer(name: str):
    return metrics.timer(name)
