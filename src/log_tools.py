import json
import os
from collections import Counter
from typing import Optional

import requests
from langchain.agents import tool
from pydantic import BaseModel, Field


class LogQueryInput(BaseModel):
    service: str = Field(description="服务名称")
    environment: str = Field(description="环境，例如production或staging")
    start_time: str = Field(description="查询开始时间，ISO-8601格式")
    end_time: str = Field(description="查询结束时间，ISO-8601格式")
    keyword: Optional[str] = Field(default=None, description="错误码或检索关键词")


class LogTextInput(BaseModel):
    log_text: str = Field(description="需要分析的原始日志文本")


@tool
def query_logs(query: LogQueryInput) -> str:
    """从配置的内部日志查询接口读取指定服务和时间范围的日志。"""
    endpoint = os.getenv("LOG_API_URL")
    if not endpoint:
        return json.dumps(
            {
                "status": "unavailable",
                "error_type": "configuration_error",
                "message": "LOG_API_URL未配置，无法查询日志平台。",
            },
            ensure_ascii=False,
        )

    headers = {}
    token = os.getenv("LOG_API_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        response = requests.get(
            endpoint,
            params=query.model_dump(exclude_none=True),
            headers=headers,
            timeout=float(os.getenv("LOG_API_TIMEOUT_SECONDS", "8")),
        )
        response.raise_for_status()
        return json.dumps(
            {"status": "success", "data": response.json()},
            ensure_ascii=False,
            default=str,
        )
    except requests.Timeout:
        return json.dumps(
            {
                "status": "error",
                "error_type": "timeout",
                "retryable": True,
                "message": "日志平台查询超时。",
            },
            ensure_ascii=False,
        )
    except requests.RequestException as exc:
        return json.dumps(
            {
                "status": "error",
                "error_type": "log_api_error",
                "retryable": False,
                "message": str(exc),
            },
            ensure_ascii=False,
        )


@tool
def summarize_log_text(payload: LogTextInput) -> str:
    """对用户提供的日志文本做基础错误级别统计，帮助后续故障诊断。"""
    lines = [line.strip() for line in payload.log_text.splitlines() if line.strip()]
    levels = Counter()
    suspicious_lines = []
    for line in lines:
        upper = line.upper()
        matched = next((level for level in ("FATAL", "ERROR", "WARN") if level in upper), None)
        if matched:
            levels[matched] += 1
            if len(suspicious_lines) < 20:
                suspicious_lines.append(line)

    return json.dumps(
        {
            "status": "success",
            "line_count": len(lines),
            "level_counts": dict(levels),
            "suspicious_lines": suspicious_lines,
        },
        ensure_ascii=False,
    )
