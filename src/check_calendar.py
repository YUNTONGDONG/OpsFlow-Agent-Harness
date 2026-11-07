import os
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
import pickle
from datetime import datetime, timedelta

# Google Calendar API scopes
SCOPES = ['https://www.googleapis.com/auth/calendar']

def check_calendar_access():
    """检查 Google Calendar 的访问权限"""
    creds = None
    
    # 检查 token.pickle 文件
    if os.path.exists('token.pickle'):
        print("找到 token.pickle 文件")
        with open('token.pickle', 'rb') as token:
            creds = pickle.load(token)
            print("成功加载凭证")
    else:
        print("未找到 token.pickle 文件")
        return False
    
    # 检查凭证是否有效
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("凭证已过期，尝试刷新")
            creds.refresh(Request())
        else:
            print("凭证无效或已过期")
            return False
    
    try:
        # 创建 Calendar API 服务
        service = build('calendar', 'v3', credentials=creds)
        
        # 获取日历列表
        calendar_list = service.calendarList().list().execute()
        print("\n可用的日历:")
        for calendar in calendar_list['items']:
            print(f"- {calendar['summary']} (ID: {calendar['id']})")
        
        # 获取未来24小时的事件
        now = datetime.utcnow()
        time_min = now.isoformat() + 'Z'
        time_max = (now + timedelta(days=1)).isoformat() + 'Z'
        
        events_result = service.events().list(
            calendarId='primary',
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        
        events = events_result.get('items', [])
        
        print("\n未来24小时的事件:")
        if not events:
            print("没有找到任何事件")
        for event in events:
            start = event['start'].get('dateTime', event['start'].get('date'))
            print(f"- {event['summary']} (开始时间: {start})")
        
        return True
        
    except Exception as e:
        print(f"发生错误: {str(e)}")
        return False

if __name__ == "__main__":
    print("开始检查 Google Calendar 访问权限...")
    check_calendar_access() 