import openai
import os
import re
import json
import time
import requests
from pathlib import Path

# 异常定义
class FileTooLargeError(Exception): pass
class APIConnectionError(Exception): pass
class APIResponseError(Exception): pass
class InvalidSessionError(Exception): pass
class ConfigLoadError(Exception): pass

# 最大文件大小限制（1MB）
MAX_FILE_SIZE = 1024 * 1024

# 最大消息长度限制
MAX_MESSAGE_LENGTH = 5000

# 历史记录目录
HISTORY_DIR = Path("chat_history")
HISTORY_DIR.mkdir(exist_ok=True)

class ChatConfig:
    def __init__(self, name, api_base, api_key, model, request_type="openai", headers=None, is_infini=False):
        self.name = name
        self.api_base = api_base
        self.api_key = api_key
        self.model = model
        self.request_type = request_type
        self.headers = headers or {}
        self.is_infini = is_infini
    
    def __str__(self):
        return f"{self.name} ({self.model})"
    
    def to_dict(self):
        return {
            "name": self.name,
            "api_base": self.api_base,
            "api_key": self.api_key,
            "model": self.model,
            "request_type": self.request_type,
            "headers": self.headers,
            "is_infini": self.is_infini
        }

def load_configs(config_filename):
    """从配置文件加载所有配置"""
    configs = []
    
    if not os.path.exists(config_filename):
        raise FileNotFoundError(f"配置文件不存在: {config_filename}")
    
    try:
        with open(config_filename, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                
                parts = line.split('::', 6)
                if len(parts) < 4:
                    continue
                
                name = parts[0].strip()
                api_base = parts[1].strip()
                api_key = parts[2].strip()
                model = parts[3].strip()
                
                request_type = "openai"
                if len(parts) > 4:
                    request_type = parts[4].strip().lower()
                    if request_type not in ["openai", "curl"]:
                        request_type = "openai"
                
                headers = {}
                if len(parts) > 5:
                    headers_str = parts[5].strip()
                    if headers_str:
                        try:
                            headers_str = headers_str.replace("'", '"')
                            headers = json.loads(headers_str)
                        except:
                            pass
                
                is_infini = False
                if len(parts) > 6:
                    infini_str = parts[6].strip().lower()
                    if infini_str == "infini" or infini_str == "true":
                        is_infini = True
                
                config = ChatConfig(name, api_base, api_key, model, request_type, headers, is_infini)
                configs.append(config)
    except Exception as e:
        raise ConfigLoadError(f"加载配置文件出错: {str(e)}")
    
    if not configs:
        raise ConfigLoadError("配置文件中未找到有效的API配置")
    
    return configs

def attach_file(content, file_path):
    """将文件内容嵌入到字符串中"""
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"文件不存在: {file_path}")
    
    file_size = os.path.getsize(file_path)
    if file_size > MAX_FILE_SIZE:
        raise FileTooLargeError(f"文件过大(>{MAX_FILE_SIZE/1024}KB): {file_path}")
    
    with open(file_path, 'r', encoding='utf-8') as f:
        file_content = f.read()
    
    return content.replace(f"{{{{:F{file_path}}}}}", f"\n```文件内容:{file_path}\n{file_content}\n```\n")

def save_session(messages, filename):
    """保存对话历史到文件"""
    if not filename.endswith('.json'):
        filename += '.json'
    
    file_path = HISTORY_DIR / filename
    
    # 尝试获取对话标题
    title = "未命名对话"
    for msg in messages:
        if msg['role'] == 'user':
            title = msg['content'].replace('\n', ' ')[:20] + "..."
            break
    
    # 查找模型信息
    model = "unknown"
    for msg in messages:
        if msg['role'] == 'assistant' and 'model' in msg.get('metadata', {}):
            model = msg['metadata']['model']
            break
    
    data = {
        'timestamp': int(time.time()),
        'title': title,
        'model': model,
        'messages': messages
    }
    
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return file_path
    except Exception as e:
        raise RuntimeError(f"保存失败: {str(e)}")

def load_session(filename):
    """从文件加载对话历史"""
    if not filename.endswith('.json'):
        filename += '.json'
    
    file_path = HISTORY_DIR / filename
    
    if not file_path.exists():
        raise FileNotFoundError(f"历史文件不存在: {file_path}")
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data['messages']
    except Exception as e:
        raise InvalidSessionError(f"加载失败: {str(e)}")

def _embed_file_content(content):
    """内部函数：替换输入字符串中的文件标记为文件内容"""
    pattern = r'\{\{:F([^}]+)\}\}'
    matches = re.findall(pattern, content)
    
    for file_path in matches:
        file_path = file_path.strip()
        try:
            if not os.path.exists(file_path):
                raise FileNotFoundError(f"文件不存在: {file_path}")
            
            file_size = os.path.getsize(file_path)
            if file_size > MAX_FILE_SIZE:
                raise FileTooLargeError(f"文件过大(>{MAX_FILE_SIZE/1024}KB): {file_path}")
            
            with open(file_path, 'r', encoding='utf-8') as f:
                file_content = f.read()
            
            content = content.replace(f"{{{{:F{file_path}}}}}", f"\n```文件内容:{file_path}\n{file_content}\n```\n")
        except Exception as e:
            raise e
    
    return content

def _send_openai_request(config, messages):
    """使用OpenAI库发送请求"""
    full_response = ""
    
    try:
        response = openai.ChatCompletion.create(
            model=config.model,
            messages=messages,
            stream=True,
            api_base=config.api_base,
            api_key=config.api_key,
            headers=config.headers
        )
        
        for chunk in response:
            if 'choices' in chunk and len(chunk['choices']) > 0:
                choice = chunk['choices'][0]
                if 'delta' in choice and 'content' in choice['delta']:
                    content = choice['delta']['content']
                    full_response += content
        
        if not full_response:
            raise APIResponseError("AI未返回有效响应")
        
        return full_response
    
    except openai.error.APIError as e:
        raise APIConnectionError(f"API错误: {str(e)}")
    except Exception as e:
        raise APIConnectionError(f"请求错误: {str(e)}")

def _send_curl_request(config, messages):
    """使用Requests库发送自定义请求"""
    full_response = ""
    
    try:
        if config.is_infini:
            payload = {"model": config.model, "messages": messages}
        else:
            payload = {"model": config.model, "messages": messages, "stream": True}
        
        headers = {
            "Authorization": f"Bearer {config.api_key}",
            "Content-Type": "application/json"
        }
        
        if config.headers:
            headers.update(config.headers)
        
        response = requests.post(
            config.api_base,
            json=payload,
            headers=headers,
            stream=not config.is_infini
        )
        
        if response.status_code != 200:
            raise APIResponseError(f"HTTP {response.status_code} - {response.text}")
        
        # 处理Infini格式的非流式响应
        if config.is_infini:
            data = response.json()
            if "choices" in data and len(data["choices"]) > 0:
                choice = data["choices"][0]
                if "message" in choice and "content" in choice["message"]:
                    full_response = choice["message"]["content"]
                else:
                    raise APIResponseError("API响应格式不兼容")
            else:
                raise APIResponseError("AI未返回有效响应")
            return full_response
        
        # 处理流式响应
        for line in response.iter_lines():
            if not line:
                continue
            
            try:
                line_str = line.decode('utf-8')
                if line_str.startswith("data: "):
                    line_str = line_str[6:]
                
                data = json.loads(line_str)
                
                if "choices" in data and len(data["choices"]) > 0:
                    choice = data["choices"][0]
                    if "delta" in choice and "content" in choice["delta"]:
                        content = choice["delta"]["content"]
                        full_response += content
                
                if data.get("done", False) or data.get("finish_reason", None):
                    break
                    
            except json.JSONDecodeError:
                continue
            except Exception as e:
                raise APIResponseError(f"解析错误: {str(e)}")
        
        if not full_response:
            raise APIResponseError("AI未返回有效响应")
        
        return full_response
    
    except requests.exceptions.RequestException as e:
        raise APIConnectionError(f"网络错误: {str(e)}")
    except Exception as e:
        raise APIConnectionError(f"请求错误: {str(e)}")

def run_chat_session(configs, session, config_index=0):
    """
    执行聊天会话并返回AI回复
    
    参数:
        configs: 预加载的API配置列表
        session: 聊天会话消息列表
        config_index: 要使用的API配置索引
        
    返回:
        (完整会话, AI回复文本)
    """
    if not session:
        raise ValueError("会话不能为空")
    
    if config_index < 0 or config_index >= len(configs):
        raise IndexError(f"无效的配置索引: {config_index}")
    
    config = configs[config_index]
    
    # 处理文件标记
    processed_session = []
    for msg in session:
        if msg['role'] == 'user':
            try:
                # 处理文件标记
                content = _embed_file_content(msg['content'])
                # 截断过长的消息
                if len(content) > MAX_MESSAGE_LENGTH:
                    content = content[:MAX_MESSAGE_LENGTH] + "\n...（消息过长，已截断）"
                processed_session.append({"role": "user", "content": content})
            except Exception as e:
                raise e
        else:
            processed_session.append(msg)
    
    # 只保留最近的10条消息
    messages_to_send = processed_session[-10:]
    
    # 发送请求
    try:
        if config.request_type == "curl":
            full_response = _send_curl_request(config, messages_to_send)
        else:
            full_response = _send_openai_request(config, messages_to_send)
    except Exception as e:
        raise e
    
    # 添加元数据到回复消息
    full_response_msg = {
        "role": "assistant",
        "content": full_response,
        "metadata": {
            "model": config.model,
            "provider": config.name,
            "timestamp": int(time.time())
        }
    }
    
    # 更新会话
    updated_session = session.copy()
    updated_session.append(full_response_msg)
    
    return updated_session, full_response