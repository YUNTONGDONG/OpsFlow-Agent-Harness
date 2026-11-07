from typing import Optional
import os
import time
import requests
from pydantic import BaseModel, Field
from dotenv import load_dotenv
from langchain.agents import tool
from langchain_community.utilities import SerpAPIWrapper
from langchain_qdrant import QdrantVectorStore
from qdrant_client import QdrantClient
from langchain_openai import OpenAIEmbeddings, ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain.chains import create_history_aware_retriever, create_retrieval_chain
from langchain.chains.combine_documents import create_stuff_documents_chain
from .Memory import MemoryClass
from .Storage import get_user
from langchain_core.output_parsers import PydanticOutputParser
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
import pickle
from datetime import datetime, timedelta

# 配置管理
class Config:
    def __init__(self):
        load_dotenv()
        self.setup_environment()
        
    @staticmethod
    def setup_environment():
        required_vars = [
            "SERPAPI_API_KEY",
            "OPENAI_API_KEY",
            "OPENAI_API_BASE",
            "SLACK_BOT_TOKEN",
            "SLACK_APP_TOKEN"
        ]
        
        for var in required_vars:
            if not os.getenv(var):
                raise EnvironmentError(f"Missing required environment variable: {var}")
            
        os.environ.update({
            "SERPAPI_API_KEY": os.getenv("SERPAPI_API_KEY"),
            "OPENAI_API_KEY": os.getenv("OPENAI_API_KEY"),
            "OPENAI_API_BASE": os.getenv("OPENAI_API_BASE")
        })

# Google Calendar API scopes
SCOPES = [
    'https://www.googleapis.com/auth/calendar',
    'https://www.googleapis.com/auth/tasks'
]

class GoogleClient:
    def __init__(self):
        self.creds = None
        self.calendar_service = None
        self.tasks_service = None
        self._initialize_services()

    def _initialize_services(self):
        """Initialize Google Calendar and Tasks services"""
        if os.path.exists('token.pickle'):
            with open('token.pickle', 'rb') as token:
                self.creds = pickle.load(token)

        if not self.creds or not self.creds.valid:
            if self.creds and self.creds.expired and self.creds.refresh_token:
                self.creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(
                    'credentials.json', SCOPES)
                self.creds = flow.run_local_server(port=0)
            
            with open('token.pickle', 'wb') as token:
                pickle.dump(self.creds, token)

        self.calendar_service = build('calendar', 'v3', credentials=self.creds)
        self.tasks_service = build('tasks', 'v1', credentials=self.creds)

# Input schemas
class TodoInput(BaseModel):
    subject: str
    dueTime: Optional[str] = None
    description: Optional[str] = None
    priority: Optional[int] = None

class ScheduleSchema(BaseModel):
    startTime: str
    endTime: str

class ScheduleSchemaSet(BaseModel):
    summary: str
    description: Optional[str] = None
    start: dict
    end: dict
    isAllDay: bool = False

class ScheduleSearch(BaseModel):
    timeMin: str
    timeMax: str

class ScheduleModify(BaseModel):
    timeMin: str
    timeMax: str
    description: str
    start: str
    end: str
    summary: str

class ScheduleDel(BaseModel):
    eventid: str

# 保持原有的 Pydantic 模型定义
class ScheduleSchemaSet_data(BaseModel):
    date: Optional[str] = Field(None, description=f"日程开始日期，格式：yyyy-MM-dd,当前时间为{time.strftime('%Y-%m-%d')},说明(全天日程必须有值,非全天日程必须留空)")
    dateTime: Optional[str] = Field(None, description=f"日程开始时间，格式为HH:MM:SS，例如17:00:00表示下午5点。注意：不要包含日期，只需要时间部分。说明(全天日程必须留空,非全天日程必须有值)")
    timeZone: Optional[str] = Field(None, description=f"日程开始时间所属时区，TZ database name格式,固定为America/Los_Angeles,说明(全天日程必须留空,非全天日程必须有值)")

class ScheduleSchemaSet_data_end(BaseModel):
    date: Optional[str] = Field(None, description=f"日程结束日期，格式：yyyy-MM-dd,当前时间为{time.strftime('%Y-%m-%d')},说明（全天日程：必须有值结束时间需传 T+1例如 2024-06-01 的全天日程，开始时间为 2024-06-01，则结束时间应该写 2024-06-02。非全天日程必须留空")
    dateTime: Optional[str] = Field(None, description=f"日程结束时间，格式为HH:MM:SS，例如19:00:00表示晚上7点。注意：不要包含日期，只需要时间部分。说明（全天日程必须留空，非全天日程必须有值）")
    timeZone: Optional[str] = Field(None, description=f"日程结束时间所属时区，必须和开始时间所属时区相同，TZ database name格式,固定为America/Los_Angeles，说明（全天日程必须留空非全天日程必须有值")

class ScheduleSchemaSet(BaseModel):
    summary: str = Field(description=f"日程标题，最大不超过2048个字符")
    start: ScheduleSchemaSet_data = Field(description="日程开始时间")
    end: ScheduleSchemaSet_data_end = Field(description="日程结束时间")
    isAllDay: bool = Field(description="是否全天日程。true：是false：不是")
    description: Optional[str] = Field(None, description=f"日程描述，最大不超过5000个字符")

class ScheduleSearch(BaseModel):
    timeMin: Optional[str] = Field(None, description="日程开始时间的最小值，格式为ISO-8601的date-time格式，可不填,说明(timeMin和 timeMax最大差值为一年),当前时间为{}".format(time.strftime("%Y-%m-%dT%H:%M:%S+08:00", time.localtime())))
    timeMax: Optional[str] = Field(None, description="日程开始时间的最大值，格式为ISO-8601的date-time格式，可不填,说明(timeMin和 timeMax最大差值为一年),当前时间为{}".format(time.strftime("%Y-%m-%dT%H:%M:%S+08:00", time.localtime())))

class ScheduleModify(BaseModel):
    timeMin: Optional[str] = Field(None, description="日程开始时间的最小值，格式为ISO-8601的date-time格式，可不填,说明(timeMin和 timeMax最大差值为一年),当前时间为{}".format(time.strftime("%Y-%m-%dT%H:%M:%S+08:00", time.localtime())))
    timeMax: Optional[str] = Field(None, description="日程开始时间的最大值，格式为ISO-8601的date-time格式，可不填,说明(timeMin和 timeMax最大差值为一年),当前时间为{}".format(time.strftime("%Y-%m-%dT%H:%M:%S+08:00", time.localtime())))
    description: Optional[str] = Field(None, description=f"日程描述，最大不超过5000个字符")
    start: Optional[ScheduleSchemaSet_data] = Field(None, description="日程开始时间")
    end: Optional[ScheduleSchemaSet_data_end] = Field(None, description="日程结束时间")
    summary: Optional[str] = Field(None, description=f"日程标题，最大不超过2048个字符")

# 删除模型
class DeleteSchedule(BaseModel):
    summary: str = Field(description="日程标题")
    description: Optional[str] = Field(description="日程描述")



class EventsId(BaseModel):
    id: str = Field(description="日程id")
    isAllDay: bool = Field(description="是否全天日程")

class ScheduleDel(BaseModel):
    eventid: str = Field(description="日程id")

# 工具函数
@tool
def search(query: str) -> str:
    """只有需要了解实时信息或不知道的事情的时候才会使用这个工具."""
    serp = SerpAPIWrapper()
    return serp.run(query)


# RAG 工具，从本地知识库获取信息
@tool(parse_docstring=True)
def get_info_from_local(query: str) -> str:
    """从本地知识库获取信息。

    Args:
        query (str): 用户的查询问题

    Returns:
        str: 从知识库中检索到的答案
    """
    print("-------RAG-------------")
    userid = get_user("userid")
    print(userid)
    llm = ChatOpenAI(model=os.getenv("BASE_MODEL"))
    
    client = QdrantClient(path=os.getenv("PERSIST_DIR","./vector_store"))
    collection_name = os.getenv("EMBEDDING_COLLECTION")
    
    vector_store = QdrantVectorStore(
        client=client, 
        collection_name=collection_name,
        embedding=OpenAIEmbeddings(
            model=os.getenv("EMBEDDING_MODEL", "text-embedding-3-small"),
            api_key=os.getenv("EMBEDDING_API_KEY"),
            base_url=os.getenv("EMBEDDING_API_BASE")
        )
    )
    
    # 直接使用向量存储进行检索
    docs = vector_store.similarity_search(query, k=3)
    
    if not docs:
        return "抱歉，我在知识库中没有找到相关信息。"
    
    # 构建上下文 拼接
    context = "\n\n".join([doc.page_content for doc in docs])
    
    # 使用 LLM 生成回答
    # 防止回答时出现幻觉，所以需要提供上下文:"如果上下文中没有相关信息"
    prompt = ChatPromptTemplate.from_messages([
        ("system", "你是一个专业的AI助手，请根据以下上下文回答问题。如果上下文中没有相关信息，请直接说明无法回答。\n\n上下文：\n{context}"),
        ("human", "{input}")
    ])
    
    chain = prompt | llm
    
    response = chain.invoke({
        "input": query,
        "context": context
    })
    
    return response.content

@tool
def create_todo(todo: TodoInput) -> str:
    """创建一个待办事项
    Args:
        todo: 包含待办事项信息的对象
    Returns:
        str: 创建结果消息
    """
    client = GoogleClient()
    
    task = {
        'title': todo.subject,
        'notes': todo.description if todo.description else '',
        'status': 'needsAction'
    }
    
    if todo.dueTime:
        # 如果没有时区，自动补全为本地时区（如+08:00或Z）
        if 'Z' not in todo.dueTime and '+' not in todo.dueTime and '-' not in todo.dueTime[10:]:
            todo.dueTime = todo.dueTime + 'Z'  # 或 '+08:00'，根据你需求
        task['due'] = todo.dueTime
    
    try:
        # Get the default task list
        tasklists = client.tasks_service.tasklists().list().execute()
        tasklist_id = tasklists['items'][0]['id']
        
        # Create the task
        result = client.tasks_service.tasks().insert(
            tasklist=tasklist_id,
            body=task
        ).execute()
        
        return f"成功创建待办事项: {todo.subject}"
    except Exception as e:
        return f"创建待办事项失败: {str(e)}"

@tool
def checkSchedule(schedule: ScheduleSchema) -> str:
    """检查用户在某段时间内的忙闲状态
    Args:
        schedule: 包含查询时间范围的对象
    Returns:
        str: 查询结果消息
    """
    client = GoogleClient()
    
    try:
        events_result = client.calendar_service.events().list(
            calendarId='primary',
            timeMin=schedule.startTime,
            timeMax=schedule.endTime,
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        
        return events_result
    except Exception as e:
        return f"查询日程失败: {str(e)}"

@tool
def SetSchedule(sets: ScheduleSchemaSet) -> str:
    """创建日程
    Args:
        sets: 包含日程信息的对象
    Returns:
        str: 创建结果消息
    """
    client = GoogleClient()
    
    # 打印输入参数
    print(f"创建日程参数: {sets}")
    print(f"原始开始时间: {sets.start.dateTime}")
    print(f"原始结束时间: {sets.end.dateTime}")
    
    event = {
        'summary': sets.summary,
        'description': sets.description if sets.description else '',
    }
    
    if sets.isAllDay:
        event['start'] = {'date': sets.start.date}
        event['end'] = {'date': sets.end.date}
    else:
        # 获取当前日期
        from datetime import datetime, timedelta
        now = datetime.now()
        print(f"当前时间: {now}")
        
        try:
            # 尝试解析时间字符串
            if 'T' in sets.start.dateTime:
                # 如果是 ISO 格式，提取时间部分
                print(f"检测到ISO格式时间: {sets.start.dateTime}")
                # 强制使用当前年份
                current_year = now.year
                start_time = datetime.fromisoformat(sets.start.dateTime.replace('Z', '+00:00')).time()
                end_time = datetime.fromisoformat(sets.end.dateTime.replace('Z', '+00:00')).time()
            else:
                # 如果是 HH:MM:SS 格式
                print(f"检测到HH:MM:SS格式时间: {sets.start.dateTime}")
                start_time = datetime.strptime(sets.start.dateTime, '%H:%M:%S').time()
                end_time = datetime.strptime(sets.end.dateTime, '%H:%M:%S').time()
            
            print(f"解析后的开始时间: {start_time}")
            print(f"解析后的结束时间: {end_time}")
            
            # 创建明天的日期时间
            tomorrow = now + timedelta(days=1)
            print(f"明天日期: {tomorrow.date()}")
            
            # 强制使用当前年份的明天
            start_datetime = datetime.combine(tomorrow.date(), start_time)
            end_datetime = datetime.combine(tomorrow.date(), end_time)
            
            print(f"组合后的开始时间: {start_datetime}")
            print(f"组合后的结束时间: {end_datetime}")
            
            # 格式化为 ISO 8601，使用美国西部时间
            start_iso = start_datetime.strftime('%Y-%m-%dT%H:%M:%S-07:00')
            end_iso = end_datetime.strftime('%Y-%m-%dT%H:%M:%S-07:00')
            
            print(f"最终开始时间: {start_iso}")
            print(f"最终结束时间: {end_iso}")
            
            event['start'] = {
                'dateTime': start_iso,
                'timeZone': 'America/Los_Angeles'
            }
            event['end'] = {
                'dateTime': end_iso,
                'timeZone': 'America/Los_Angeles'
            }
            
        except ValueError as e:
            print(f"时间解析错误: {e}")
            return f"时间格式错误: {str(e)}"
    
    print(f"创建事件: {event}")
    
    try:
        # 使用主日历
        calendar_id = 'primary'
        print(f"使用日历ID: {calendar_id}")
        
        # 创建事件
        created_event = client.calendar_service.events().insert(
            calendarId=calendar_id,
            body=event
        ).execute()
        
        print(f"事件创建成功: {created_event.get('htmlLink')}")
        return f"成功创建日程: {sets.summary}\n你可以在 Google Calendar 中查看: {created_event.get('htmlLink')}"
    except Exception as e:
        print(f"创建事件失败: {str(e)}")
        return f"创建日程失败: {str(e)}"

@tool
def SearchSchedule(search: ScheduleSearch) -> str:
    """查询日程
    Args:
        search: 包含查询时间范围的对象
    Returns:
        str: 查询结果消息
    """
    client = GoogleClient()
    
    try:
        events_result = client.calendar_service.events().list(
            calendarId='primary',
            timeMin=search.timeMin,
            timeMax=search.timeMax,
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        
        return events_result
    except Exception as e:
        return f"查询日程失败: {str(e)}"

def FindPreciseOrder(orginrder: str,events:object) -> str:
    """查找精确的指令"""
    llm = ChatOpenAI(model=os.getenv("BASE_MODEL"))
    prompt = ChatPromptTemplate.from_messages([
        ("system", """请根据用户的输入和查询到的日程信息，提取出与用户输入最匹配的1个日程id以及是否为全天事件。注意查询到的数据结构为：{{'events':[{{
            'attendees': [],
            'categories': [],
            'createTime': '2023-09-26T08: 24: 18Z',
            'description': '',
            'end': '',
            'extendedProperties': '',
            'id': '',
            'isAllDay': False,
            'organizer': '',
            'reminders': [
            ],
            'start': '',
            'status': '',
            'summary': 'xxxxxx,
            'updateTime': ''
        }}]}} 日程id为events中的id字段，例如events[0]['id']，是否为全天事件字段为events中的isAllDay，例如events[0]['isAllDay']，有可能存在多个events项，你需要根据用户输入来匹配筛选，输出结构化数据,不要有其他输出。查询到的日程信息为：{events}"""),
        ("human", "{input}"),
    ])
    try:
        parser = PydanticOutputParser(pydantic_object=EventsId)
        prompt.partial_variables = {"format_instructions": parser.get_format_instructions()}
        chain = prompt | llm | parser
        return chain.invoke({"input": orginrder,"events":events})
    except Exception as e:
        print(e)
        return None



@tool
def ModifySchedule(search: ScheduleModify) -> str:
    """修改日程
    Args:
        search: 包含修改信息的对象
    Returns:
        str: 修改结果消息
    """
    client = GoogleClient()
    
    # First search for the event
    search_params = ScheduleSearch(
        timeMin=search.timeMin,
        timeMax=search.timeMax
    )
    
    search_result = SearchSchedule.invoke(search_params)
    if isinstance(search_result, str):
        return "查询日程失败"
    
    events = search_result.get('items', [])
    if not events:
        return "未找到相关日程"
    
    # Find the matching event
    target_event = None
    for event in events:
        if (event.get('summary') == search.summary and
            event.get('description') == search.description):
            target_event = event
            break
    
    if not target_event:
        return "未找到匹配的日程"
    
    # Update the event
    event_id = target_event['id']
    updated_event = {
        'summary': search.summary,
        'description': search.description,
    }
    
    if 'date' in target_event['start']:
        updated_event['start'] = {'date': search.start}
        updated_event['end'] = {'date': search.end}
    else:
        updated_event['start'] = {
            'dateTime': search.start,
            'timeZone': target_event['start'].get('timeZone', 'UTC')
        }
        updated_event['end'] = {
            'dateTime': search.end,
            'timeZone': target_event['end'].get('timeZone', 'UTC')
        }
    
    try:
        client.calendar_service.events().update(
            calendarId='primary',
            eventId=event_id,
            body=updated_event
        ).execute()
        
        return f"成功修改日程: {search.summary}"
    except Exception as e:
        return f"修改日程失败: {str(e)}"
    
@tool
def DelSchedule(query: DeleteSchedule) -> str:
    """当用户要求删除日程时调用此工具
    Args:
        query: 用户要删除的日程信息
    Returns:
        str: 返回给用户确认要具体删除的日程信息
    """
    # 创建 ScheduleSearch 对象并转换为字典
    search_params = ScheduleSearch()
    # 包装成正确的格式：添加 search 字段
    search_dict = {
        "search": search_params.model_dump()
    }
    # 使用 invoke 方法调用 SearchSchedule
    searchResult = SearchSchedule.invoke(search_dict)
    events = searchResult.get('items', [])
    if not events:
        return "您的日程空空如也"
    if len(events) > 1:
        orginOder = f"description: {query.description}, summary: {query.summary}"
        returnID = FindPreciseOrder(orginOder,events)
        print(returnID)
        eventid = returnID.id
        if not eventid:
            return "您的日程似乎不存在，是否输入有误？"
    else:
        eventid = events[0]['id']
    print("要删除的日程ID：",eventid)
    return f"记录下日程id,然后询问用户，是否确认要删除日程 {eventid}"

@tool
def ConfirmDelSchedule(query: ScheduleDel) -> str:
    """删除日程
    Args:
        query: 包含要删除的日程ID的对象
    Returns:
        str: 删除结果消息
    """
    client = GoogleClient()
    
    try:
        client.calendar_service.events().delete(
            calendarId='primary',
            eventId=query.eventid
        ).execute()
        
        return "成功删除日程"
    except Exception as e:
        return f"删除日程失败: {str(e)}"
        
