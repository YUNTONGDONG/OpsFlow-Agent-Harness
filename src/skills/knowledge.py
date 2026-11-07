from .base import SkillDefinition
from ..Tools import get_info_from_local, search


knowledge_search_skill = SkillDefinition(
    name="knowledge_search",
    description="检索内部知识库；只有需要外部实时信息时才使用在线搜索。",
    tools=(get_info_from_local, search),
    trigger_terms=(
        "知识库", "文档", "手册", "说明", "怎么", "如何", "为什么",
        "故障案例", "历史案例", "最新", "实时", "search", "document",
    ),
)
