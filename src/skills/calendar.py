from .base import SkillDefinition
from ..Tools import (
    ConfirmDelSchedule,
    DelSchedule,
    ModifySchedule,
    SearchSchedule,
    SetSchedule,
    checkSchedule,
)


calendar_skill = SkillDefinition(
    name="calendar_management",
    description="查询、创建、修改或删除Google Calendar日程。写操作需要谨慎执行。",
    tools=(
        checkSchedule,
        SearchSchedule,
        SetSchedule,
        ModifySchedule,
        DelSchedule,
        ConfirmDelSchedule,
    ),
    trigger_terms=(
        "日历", "日程", "会议", "开会", "安排", "预约", "空闲", "复盘",
        "calendar", "meeting", "schedule",
    ),
    read_only=False,
)
