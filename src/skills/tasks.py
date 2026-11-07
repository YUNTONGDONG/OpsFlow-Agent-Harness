from .base import SkillDefinition
from ..Tools import create_todo


task_skill = SkillDefinition(
    name="task_management",
    description="创建和管理Google Tasks待办事项。",
    tools=(create_todo,),
    trigger_terms=(
        "待办", "任务", "提醒", "跟进", "todo", "task", "remind",
    ),
    read_only=False,
)
