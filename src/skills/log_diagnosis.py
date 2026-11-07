from .base import SkillDefinition
from ..log_tools import query_logs, summarize_log_text


log_diagnosis_skill = SkillDefinition(
    name="log_diagnosis",
    description="查询服务日志或分析用户提供的日志文本，提取错误证据用于初步故障诊断。",
    tools=(query_logs, summarize_log_text),
    trigger_terms=(
        "日志", "报错", "错误码", "异常", "故障", "排查", "error", "exception",
        "stack trace", "log",
    ),
)
