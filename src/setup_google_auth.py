import os
from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
import pickle

# Google Calendar API scopes
SCOPES = [
    'https://www.googleapis.com/auth/calendar',
    'https://www.googleapis.com/auth/tasks'
]

def setup_google_auth():
    """设置 Google Calendar API 认证"""
    creds = None
    
    # 检查是否已经有 token.pickle 文件
    if os.path.exists('token.pickle'):
        with open('token.pickle', 'rb') as token:
            creds = pickle.load(token)
    
    # 如果没有有效的凭证，则获取新的凭证
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            # 检查 credentials.json 文件是否存在
            if not os.path.exists('credentials.json'):
                print("错误：找不到 credentials.json 文件")
                print("请按照以下步骤操作：")
                print("1. 访问 https://console.cloud.google.com/")
                print("2. 创建一个新项目或选择现有项目")
                print("3. 启用 Google Calendar API 和 Google Tasks API")
                print("4. 在'凭据'页面创建 OAuth 2.0 客户端 ID")
                print("5. 下载凭据并重命名为 credentials.json")
                print("6. 将 credentials.json 文件放在项目根目录下")
                return False
            
            # 使用 credentials.json 获取新的凭证
            flow = InstalledAppFlow.from_client_secrets_file(
                'credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)
        
        # 保存凭证到 token.pickle 文件
        with open('token.pickle', 'wb') as token:
            pickle.dump(creds, token)
        
        print("成功设置 Google Calendar API 认证")
        return True
    
    print("已经存在有效的 Google Calendar API 认证")
    return True

if __name__ == "__main__":
    setup_google_auth()