# 导入必要的库和模块
from langchain.agents import AgentExecutor,create_tool_calling_agent
from langchain_openai import ChatOpenAI  # OpenAI聊天模型接口
from .Prompt import PromptClass  # 导入提示词管理类
from .Memory import MemoryClass  # 导入记忆管理类
from .Emotion import EmotionClass  # 导入情感分析类
from langchain_core.caches import InMemoryCache  # 内存缓存，用于加速响应
from .Storage import get_user  # 获取用户信息的函数

from .skill_router import SkillRouter
from .skills import build_default_registry
from dotenv import load_dotenv as _load_dotenv
_load_dotenv()
import os


os.environ["LANGSMITH_TRACING"] = os.getenv("LANGSMITH_TRACING")
os.environ["LANGSMITH_ENDPOINT"] = os.getenv("LANGSMITH_ENDPOINT")
os.environ["LANGSMITH_API_KEY"] = os.getenv("LANGSMITH_API_KEY")
os.environ["LANGSMITH_PROJECT"] = os.getenv("LANGSMITH_PROJECT")
# 设置环境变量，包括API密钥和API基础URL
os.environ["OPENAI_API_KEY"] = os.getenv("OPENAI_API_KEY")
os.environ["OPENAI_API_BASE"] = os.getenv("OPENAI_API_BASE")




# 添加缓存以提高性能，避免重复请求相同内容时消耗额外的API调用
from langchain_core.globals import set_llm_cache
set_llm_cache(InMemoryCache())


class AgentClass:
    """
    AI代理类，负责处理用户输入并生成回复
    整合了语言模型、记忆系统、情感分析和各种工具功能
    """
    def __init__(self):
        # 设置备用模型，当主模型不可用时使用
        # 获取主模型名称
        self.modelname = os.getenv("BASE_MODEL")

        # 创建主聊天模型；只有显式配置时才启用备用模型。
        primary_llm = ChatOpenAI(model=self.modelname)
        backup_model = os.getenv("BACKUP_MODEL")
        self.chatmodel = (
            primary_llm.with_fallbacks([ChatOpenAI(model=backup_model)])
            if backup_model
            else primary_llm
        )

        # Harness能力层：先路由到Skill，再只向模型暴露相关工具。
        self.skill_registry = build_default_registry()
        self.skill_router = SkillRouter(self.skill_registry)
        self.tools = self.skill_registry.tools_for(self.skill_registry.names())
        
        # 从环境变量获取记忆键名
        self.memorykey = os.getenv("MEMORY_KEY")
        
        # 初始化情感状态，默认中性(5分)
        self.feeling = {"feeling":"default","score":5}
        
        # 初始化记忆系统
        self.memory = MemoryClass(memorykey=self.memorykey,model=self.modelname)
        
        # 初始化情感分析系统
        self.emotion = EmotionClass(model=self.modelname)
        
    def _skill_context(self, route) -> str:
        if not route.skill_names:
            return "当前请求不需要业务Skill，请直接回答，不要调用工具。"

        descriptions = []
        for skill_name in route.skill_names:
            skill = self.skill_registry.get(skill_name)
            descriptions.append(f"- {skill.name}: {skill.description}")

        plan = []
        for step in route.task_plan:
            dependency = ", ".join(step.depends_on) if step.depends_on else "无"
            plan.append(
                f"- {step.step_id}: 使用{step.skill_name}，依赖步骤：{dependency}"
            )

        return (
            "本次请求已选择以下Skill：\n"
            + "\n".join(descriptions)
            + "\n执行计划：\n"
            + "\n".join(plan)
            + "\n仅使用本次加载的工具；多步骤请求按计划顺序执行。"
        )

    def run_agent(self, input, session_id=None, user_id=None):
        """
        运行AI代理处理用户输入
        
        参数:
            input: 用户输入的文本
            session_id: 会话ID，用于标识不同的对话上下文
            
        返回:
            包含AI回复的字典
        """
        # 先选择Skill，避免每次请求把全部工具Schema交给模型。
        route = self.skill_router.route(input)
        selected_tools = self.skill_registry.tools_for(route.skill_names)

        # 进行情感分析，了解用户当前的情绪状态
        self.feeling = self.emotion.Emotion_Sensing(input)

        prompt = PromptClass(
            memorykey=self.memorykey,
            feeling=self.feeling,
        ).Prompt_Structure(skill_context=self._skill_context(route))

        agent = create_tool_calling_agent(
            self.chatmodel,
            selected_tools,
            prompt,
        )
        agent_chain = AgentExecutor(
            agent=agent,
            tools=selected_tools,
            memory=self.memory.set_memory(session_id=session_id),
            verbose=True,
            max_iterations=8,
        )

        res = agent_chain.invoke(
            {"input": input}
        )
        res["skill_route"] = route.model_dump()
        return res

