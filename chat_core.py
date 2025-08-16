import openai
import os
import re
import json
import time
import requests
import logging
from pathlib import Path
from datetime import datetime
import fcntl  # 用于文件锁定

# 设置日志
LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / "chat_core.log"
AI_OUTPUT_LOG = LOG_DIR / "AIoutput.log"  # AI输出日志文件

# 配置日志
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("chat_core")

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

def _write_ai_log(entry):
    """
    将AI输出条目写入日志文件
    新格式: timestamp - model - provider - content - streaming
    """
    try:
        # 确保日志目录存在
        AI_OUTPUT_LOG.parent.mkdir(parents=True, exist_ok=True)
        
        # 构建新格式的日志行
        timestamp = entry.get("timestamp", "")
        model = entry.get("model", "")
        provider = entry.get("provider", "")
        content = entry.get("content", "")
        streaming = "true" if entry.get("streaming", False) else "false"
        
        # 替换换行符为空格以保持单行格式
        content = content.replace('\n', '\\n').replace('\r', '\\r')
        
        # 构建日志行
        log_line = f"{timestamp} - {model} - {provider} - {content} - {streaming}\n"
        
        with open(AI_OUTPUT_LOG, 'a', encoding='utf-8') as log_file:
            # 获取文件锁
            fcntl.flock(log_file, fcntl.LOCK_EX)
            
            # 写入格式化的日志行
            log_file.write(log_line)
            log_file.flush()  # 强制刷新缓冲区
            
            # 释放文件锁
            fcntl.flock(log_file, fcntl.LOCK_UN)
            
    except IOError as e:
        logger.error(f"写入AI输出日志失败: {str(e)}")
    except Exception as e:
        logger.error(f"AI日志写入意外错误: {str(e)}")

def _log_ai_output(config, content, is_streaming=True):
    """
    记录AI输出到专用日志
    
    参数:
        config: 当前使用的配置
        content: 要记录的内容
        is_streaming: 是否为流式输出
    """
    try:
        # 获取带毫秒的时间戳
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
        
        if content:
            # 构建日志条目
            entry = {
                "timestamp": timestamp,
                "model": config.model,
                "provider": config.name,
                "content": content,
                "streaming": is_streaming
            }
            
            # 写入日志
            _write_ai_log(entry)
        
    except Exception as e:
        logger.error(f"创建AI日志条目失败: {str(e)}")

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
        logger.error(f"配置文件不存在: {config_filename}")
        raise FileNotFoundError(f"配置文件不存在: {config_filename}")
    
    try:
        logger.info(f"开始加载配置文件: {config_filename}")
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
                            logger.warning(f"解析头部JSON失败: {headers_str}")
                            pass
                
                is_infini = False
                if len(parts) > 6:
                    infini_str = parts[6].strip().lower()
                    if infini_str == "infini" or infini_str == "true":
                        is_infini = True
                
                config = ChatConfig(name, api_base, api_key, model, request_type, headers, is_infini)
                configs.append(config)
                logger.info(f"加载配置: {config}")
    except Exception as e:
        logger.error(f"加载配置文件出错: {str(e)}")
        raise ConfigLoadError(f"加载配置文件出错: {str(e)}")
    
    if not configs:
        logger.error("配置文件中未找到有效的API配置")
        raise ConfigLoadError("配置文件中未找到有效的API配置")
    
    logger.info(f"成功加载 {len(configs)} 个API配置")
    return configs

def attach_file(content, file_path):
    """将文件内容嵌入到字符串中"""
    logger.info(f"尝试嵌入文件: {file_path}")
    
    if not os.path.exists(file_path):
        logger.error(f"文件不存在: {file_path}")
        raise FileNotFoundError(f"文件不存在: {file_path}")
    
    file_size = os.path.getsize(file_path)
    if file_size > MAX_FILE_SIZE:
        logger.error(f"文件过大(>{MAX_FILE_SIZE/1024}KB): {file_path}")
        raise FileTooLargeError(f"文件过大(>{MAX_FILE_SIZE/1024}KB): {file_path}")
    
    with open(file_path, 'r', encoding='utf-8') as f:
        file_content = f.read()
    
    logger.info(f"成功嵌入文件内容: {file_path} ({len(file_content)} 字符)")
    return content.replace(f"{{{{:F{file_path}}}}}", f"\n```文件内容:{file_path}\n{file_content}\n```\n")

def save_session(messages, filename):
    """保存对话历史到文件"""
    logger.info(f"尝试保存会话到文件: {filename}")
    
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
        logger.info(f"会话已保存到: {file_path}")
        return file_path
    except Exception as e:
        logger.error(f"保存失败: {str(e)}")
        raise RuntimeError(f"保存失败: {str(e)}")

def load_session(filename):
    """从文件加载对话历史"""
    logger.info(f"尝试加载会话文件: {filename}")
    
    if not filename.endswith('.json'):
        filename += '.json'
    
    file_path = HISTORY_DIR / filename
    
    if not file_path.exists():
        logger.error(f"历史文件不存在: {file_path}")
        raise FileNotFoundError(f"历史文件不存在: {file_path}")
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        logger.info(f"成功加载会话: {file_path} ({len(data['messages'])} 条消息)")
        return data['messages']
    except Exception as e:
        logger.error(f"加载失败: {str(e)}")
        raise InvalidSessionError(f"加载失败: {str(e)}")

def _embed_file_content(content):
    """内部函数：替换输入字符串中的文件标记为文件内容"""
    pattern = r'\{\{:F([^}]+)\}\}'
    matches = re.findall(pattern, content)
    
    for file_path in matches:
        file_path = file_path.strip()
        try:
            logger.debug(f"处理文件标记: {file_path}")
            
            if not os.path.exists(file_path):
                raise FileNotFoundError(f"文件不存在: {file_path}")
            
            file_size = os.path.getsize(file_path)
            if file_size > MAX_FILE_SIZE:
                raise FileTooLargeError(f"文件过大(>{MAX_FILE_SIZE/1024}KB): {file_path}")
            
            with open(file_path, 'r', encoding='utf-8') as f:
                file_content = f.read()
            
            content = content.replace(f"{{{{:F{file_path}}}}}", f"\n```文件内容:{file_path}\n{file_content}\n```\n")
            logger.debug(f"文件内容嵌入成功: {file_path}")
        except Exception as e:
            logger.error(f"文件内容嵌入失败: {file_path} - {str(e)}")
            raise e
    
    return content

def _send_openai_request(config, messages):
    full_response = ""
    logger.info(f"发送OpenAI请求到 {config.api_base} (模型: {config.model})")
    
    try:
        # 使用新版OpenAI客户端
        client = openai.OpenAI(
            base_url=config.api_base,
            api_key=config.api_key,
            timeout=30.0,
            default_headers=config.headers
        )
        
        # 创建流式响应
        stream = client.chat.completions.create(
            model=config.model,
            messages=messages,
            stream=True
        )
        
        logger.debug("开始接收流式响应...")
        
        # 处理流式响应
        for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                content = chunk.choices[0].delta.content
                # 空值检查
                if content is None:
                    content = ""
                    
                full_response += content
                
                # 记录流式输出到AI日志
                try:
                    _log_ai_output(config, content)
                except Exception as e:
                    logger.error(f"记录流式输出失败: {str(e)}")
        
        if not full_response:
            logger.warning("AI未返回有效响应")
            raise APIResponseError("AI未返回有效响应")
        
        logger.info(f"成功接收响应: {len(full_response)} 字符")
        return full_response
    
    except openai.APIError as e:
        logger.error(f"OpenAI API错误: {str(e)}")
        raise APIConnectionError(f"API错误: {str(e)}")
    except openai.APIConnectionError as e:
        logger.error(f"OpenAI连接错误: {str(e)}")
        raise APIConnectionError(f"连接错误: {str(e)}")
    except Exception as e:
        logger.error(f"OpenAI请求错误: {str(e)}")
        raise APIConnectionError(f"请求错误: {str(e)}")

def _send_curl_request(config, messages):
    """使用Requests库发送自定义请求"""
    full_response = ""
    logger.info(f"发送CURL请求到 {config.api_base} (模型: {config.model})")
    
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
        
        logger.debug(f"请求头: {headers}")
        logger.debug(f"请求体: {json.dumps(payload, ensure_ascii=False)[:500]}...")
        
        response = requests.post(
            config.api_base,
            json=payload,
            headers=headers,
            stream=not config.is_infini
        )
        
        if response.status_code != 200:
            logger.error(f"API响应错误: HTTP {response.status_code} - {response.text[:500]}")
            raise APIResponseError(f"HTTP {response.status_code} - {response.text}")
        
        # 处理Infini格式的非流式响应
        if config.is_infini:
            logger.debug("处理Infini格式响应")
            data = response.json()
            if "choices" in data and len(data["choices"]) > 0:
                choice = data["choices"][0]
                if "message" in choice and "content" in choice["message"]:
                    full_response = choice["message"]["content"]
                else:
                    logger.warning("API响应格式不兼容")
                    raise APIResponseError("API响应格式不兼容")
            else:
                logger.warning("AI未返回有效响应")
                raise APIResponseError("AI未返回有效响应")
            
            # 记录完整响应到AI日志
            try:
                _log_ai_output(config, full_response, is_streaming=False)
            except Exception as e:
                logger.error(f"记录完整响应失败: {str(e)}")
                
            return full_response
        
        # 处理流式响应
        logger.debug("开始处理流式响应...")
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
                        
                        # 记录流式输出到AI日志
                        try:
                            _log_ai_output(config, content)
                        except Exception as e:
                            logger.error(f"记录流式输出失败: {str(e)}")
                
                if data.get("done", False) or data.get("finish_reason", None):
                    break
                    
            except json.JSONDecodeError:
                continue
            except Exception as e:
                logger.error(f"响应解析错误: {str(e)}")
                raise APIResponseError(f"解析错误: {str(e)}")
        
        if not full_response:
            logger.warning("AI未返回有效响应")
            raise APIResponseError("AI未返回有效响应")
        
        logger.info(f"成功接收响应: {len(full_response)} 字符")
        return full_response
    
    except requests.exceptions.RequestException as e:
        logger.error(f"网络错误: {str(e)}")
        raise APIConnectionError(f"网络错误: {str(e)}")
    except Exception as e:
        logger.error(f"请求错误: {str(e)}")
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
    logger.info("开始聊天会话")
    
    if not session:
        logger.error("会话不能为空")
        raise ValueError("会话不能为空")
    
    if config_index < 0 or config_index >= len(configs):
        logger.error(f"无效的配置索引: {config_index} (可用配置数: {len(configs)})")
        raise IndexError(f"无效的配置索引: {config_index} (可用配置数: {len(configs)})")
    
    config = configs[config_index]
    logger.info(f"使用配置: {config.name} ({config.model}), 请求类型: {config.request_type}")
    
    # 处理文件标记
    processed_session = []
    for msg in session:
        if msg['role'] == 'user':
            try:
                logger.debug(f"处理用户消息: {msg['content'][:100]}...")
                # 处理文件标记
                content = _embed_file_content(msg['content'])
                # 截断过长的消息
                if len(content) > MAX_MESSAGE_LENGTH:
                    logger.warning(f"消息过长({len(content)}字符)，已截断")
                    content = content[:MAX_MESSAGE_LENGTH] + "\n...（消息过长，已截断）"
                processed_session.append({"role": "user", "content": content})
            except Exception as e:
                logger.error(f"处理消息失败: {str(e)}")
                raise e
        else:
            processed_session.append(msg)
    
    # 只保留最近的10条消息
    messages_to_send = processed_session[-10:]
    logger.info(f"发送 {len(messages_to_send)} 条消息到API")
    
    # 发送请求
    try:
        if config.request_type == "curl":
            full_response = _send_curl_request(config, messages_to_send)
        else:
            full_response = _send_openai_request(config, messages_to_send)
    except Exception as e:
        logger.error(f"API请求失败: {str(e)}")
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
    
    logger.info(f"聊天会话完成，回复长度: {len(full_response)} 字符")
    return updated_session, full_response