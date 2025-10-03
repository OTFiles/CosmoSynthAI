import openai
import os
import re
import json
import time
import requests
import logging
from pathlib import Path
from datetime import datetime
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, Callable, Union, Tuple
import fcntl

# ==================== 异常体系 ====================
class ChatCoreError(Exception):
    """所有ChatCore异常的基类"""
    pass

class FileTooLargeError(ChatCoreError):
    pass

class APIConnectionError(ChatCoreError):
    pass

class APIResponseError(ChatCoreError):
    pass

class InvalidSessionError(ChatCoreError):
    pass

class ConfigLoadError(ChatCoreError):
    pass

class ToolExecutionError(ChatCoreError):
    pass

# ==================== 抽象基类 ====================
class ConfigManager(ABC):
    """配置管理器抽象基类"""
    
    @abstractmethod
    def load_configs(self, config_source: Any) -> List['APIConfig']:
        pass
    
    @abstractmethod
    def get_config(self, index: int) -> 'APIConfig':
        pass
    
    @abstractmethod
    def list_configs(self) -> List['APIConfig']:
        pass

class FileProcessor(ABC):
    """文件处理器抽象基类"""
    
    @abstractmethod
    def process_file_embeddings(self, content: str) -> str:
        pass
    
    @abstractmethod
    def validate_file(self, file_path: str) -> bool:
        pass

class APIClient(ABC):
    """API客户端抽象基类"""
    
    @abstractmethod
    def send_request(self, config: 'APIConfig', messages: List[Dict], tools: Optional[List[Dict]] = None) -> str:
        pass

class SessionManager(ABC):
    """会话管理器抽象基类"""
    
    @abstractmethod
    def save_session(self, messages: List[Dict], filename: str) -> Path:
        pass
    
    @abstractmethod
    def load_session(self, filename: str) -> List[Dict]:
        pass

class Logger(ABC):
    """日志记录器抽象基类"""
    
    @abstractmethod
    def log_ai_output(self, config: 'APIConfig', content: str, is_streaming: bool = True) -> None:
        pass
    
    @abstractmethod
    def log_error(self, message: str, exception: Optional[Exception] = None) -> None:
        pass
    
    @abstractmethod
    def log_info(self, message: str) -> None:
        pass

# ==================== 工具调用回调接口 ====================
class ToolCallbacks:
    """
    工具回调函数容器
    主程序通过实现这些回调函数来定义具体的工具行为
    """
    
    def __init__(self):
        self.tool_schemas: List[Dict] = []
        self.tool_executors: Dict[str, Callable] = {}
    
    def register_tool(self, schema: Dict, executor: Callable) -> None:
        """注册工具schema和执行函数"""
        tool_name = schema.get("function", {}).get("name")
        if not tool_name:
            raise ValueError("工具schema必须包含function.name")
        
        self.tool_schemas.append(schema)
        self.tool_executors[tool_name] = executor
    
    def execute_tool(self, tool_name: str, arguments: Dict) -> str:
        """执行工具调用"""
        if tool_name not in self.tool_executors:
            raise ToolExecutionError(f"未注册的工具: {tool_name}")
        
        try:
            return self.tool_executors[tool_name](**arguments)
        except Exception as e:
            raise ToolExecutionError(f"工具执行失败: {str(e)}")

# ==================== 配置管理 ====================
class APIConfig:
    """API配置类"""
    
    def __init__(self, name: str, api_base: str, api_key: str, model: str, 
                 request_type: str = "openai", headers: Optional[Dict] = None,
                 use_non_streaming_response: bool = False):
        self.name = name
        self.api_base = api_base
        self.api_key = api_key
        self.model = model
        self.request_type = request_type
        self.headers = headers or {}
        self.use_non_streaming_response = use_non_streaming_response
    
    def __str__(self):
        return f"{self.name} ({self.model})"
    
    def to_dict(self) -> Dict:
        return {
            "name": self.name,
            "api_base": self.api_base,
            "api_key": self.api_key,
            "model": self.model,
            "request_type": self.request_type,
            "headers": self.headers,
            "use_non_streaming_response": self.use_non_streaming_response
        }

class JSONConfigManager(ConfigManager):
    """JSON配置文件管理器"""
    
    def __init__(self, logger: Logger):
        self.logger = logger
        self.configs: List[APIConfig] = []
    
    def load_configs(self, config_filename: str) -> List[APIConfig]:
        """从JSON文件加载配置"""
        self.configs = []
        
        if not os.path.exists(config_filename):
            self.logger.log_error(f"配置文件不存在: {config_filename}")
            raise FileNotFoundError(f"配置文件不存在: {config_filename}")
        
        try:
            self.logger.log_info(f"开始加载配置文件: {config_filename}")
            with open(config_filename, 'r', encoding='utf-8') as f:
                config_data = json.load(f)
            
            if "configs" not in config_data:
                self.logger.log_error("配置文件缺少'configs'数组")
                raise ConfigLoadError("配置文件缺少'configs'数组")
            
            for config_obj in config_data["configs"]:
                required_fields = ["name", "api_base", "api_key", "model"]
                for field in required_fields:
                    if field not in config_obj:
                        self.logger.log_error(f"配置缺少必需字段: {field}")
                        raise ConfigLoadError(f"配置缺少必需字段: {field}")
                
                config = APIConfig(
                    name=config_obj["name"],
                    api_base=config_obj["api_base"],
                    api_key=config_obj["api_key"],
                    model=config_obj["model"],
                    request_type=config_obj.get("request_type", "openai"),
                    headers=config_obj.get("headers", {}),
                    use_non_streaming_response=config_obj.get("use_non_streaming_response", False)
                )
                self.configs.append(config)
                self.logger.log_info(f"加载配置: {config}")
                
        except json.JSONDecodeError as e:
            self.logger.log_error(f"JSON解析错误: {str(e)}")
            raise ConfigLoadError(f"JSON解析错误: {str(e)}")
        except Exception as e:
            self.logger.log_error(f"加载配置文件出错: {str(e)}")
            raise ConfigLoadError(f"加载配置文件出错: {str(e)}")
        
        if not self.configs:
            self.logger.log_error("配置文件中未找到有效的API配置")
            raise ConfigLoadError("配置文件中未找到有效的API配置")
        
        self.logger.log_info(f"成功加载 {len(self.configs)} 个API配置")
        return self.configs
    
    def get_config(self, index: int) -> APIConfig:
        if index < 0 or index >= len(self.configs):
            raise IndexError(f"无效的配置索引: {index}")
        return self.configs[index]
    
    def list_configs(self) -> List[APIConfig]:
        return self.configs.copy()

# ==================== 文件处理 ====================
class DefaultFileProcessor(FileProcessor):
    """默认文件处理器"""
    
    def __init__(self, max_file_size: int = 1024 * 1024, logger: Optional[Logger] = None):
        self.max_file_size = max_file_size
        self.logger = logger
    
    def validate_file(self, file_path: str) -> bool:
        """验证文件是否可处理"""
        if not os.path.exists(file_path):
            if self.logger:
                self.logger.log_error(f"文件不存在: {file_path}")
            return False
        
        file_size = os.path.getsize(file_path)
        if file_size > self.max_file_size:
            if self.logger:
                self.logger.log_error(f"文件过大: {file_path}")
            return False
        
        return True
    
    def process_file_embeddings(self, content: str) -> str:
        """处理文件嵌入标记"""
        pattern = r'\{\{:F([^}]+)\}\}'
        matches = re.findall(pattern, content)
        
        for file_path in matches:
            file_path = file_path.strip()
            try:
                if self.logger:
                    self.logger.log_info(f"处理文件标记: {file_path}")
                
                if not self.validate_file(file_path):
                    continue
                
                with open(file_path, 'r', encoding='utf-8') as f:
                    file_content = f.read()
                
                content = content.replace(
                    f"{{{{:F{file_path}}}}}", 
                    f"\n```文件内容:{file_path}\n{file_content}\n```\n"
                )
                
                if self.logger:
                    self.logger.log_info(f"文件内容嵌入成功: {file_path}")
                    
            except Exception as e:
                if self.logger:
                    self.logger.log_error(f"文件内容嵌入失败: {file_path} - {str(e)}")
        
        return content

# ==================== API客户端实现 ====================
class OpenAIClient(APIClient):
    """OpenAI API客户端"""
    
    def __init__(self, logger: Logger):
        self.logger = logger
        self.openai_version = self._get_openai_version()
        self.logger.log_info(f"检测到OpenAI版本: {self.openai_version}")
    
    def _get_openai_version(self):
        """获取OpenAI库版本"""
        try:
            return openai.__version__
        except AttributeError:
            return "0.28.1"
    
    def send_request(self, config: APIConfig, messages: List[Dict], tools: Optional[List[Dict]] = None) -> str:
        """发送OpenAI API请求"""
        full_response = ""
        self.logger.log_info(f"发送OpenAI请求到 {config.api_base} (模型: {config.model})")
        
        try:
            request_params = {
                "model": config.model,
                "messages": messages,
                "stream": True
            }
            
            # 添加工具调用参数
            if tools:
                request_params["tools"] = tools
                self.logger.log_info(f"启用工具调用，工具数量: {len(tools)}")
            
            if self.openai_version.startswith("0."):
                # 旧版本OpenAI库
                openai.api_base = config.api_base
                openai.api_key = config.api_key
                
                response = openai.ChatCompletion.create(
                    **request_params,
                    headers=config.headers
                )
                
                for chunk in response:
                    if "choices" in chunk and len(chunk["choices"]) > 0:
                        delta = chunk["choices"][0].get("delta", {})
                        if "content" in delta and delta["content"]:
                            content = delta["content"]
                            full_response += content
                            self.logger.log_ai_output(config, content)
                
            else:
                # 新版本OpenAI库
                client = openai.OpenAI(
                    base_url=config.api_base,
                    api_key=config.api_key,
                    timeout=30.0,
                    default_headers=config.headers
                )
                
                stream = client.chat.completions.create(**request_params)
                
                for chunk in stream:
                    if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                        content = chunk.choices[0].delta.content or ""
                        full_response += content
                        self.logger.log_ai_output(config, content)
            
            if not full_response:
                self.logger.log_error("AI未返回有效响应")
                raise APIResponseError("AI未返回有效响应")
            
            self.logger.log_info(f"成功接收响应: {len(full_response)} 字符")
            return full_response
        
        except openai.APIError as e:
            self.logger.log_error(f"OpenAI API错误: {str(e)}")
            raise APIConnectionError(f"API错误: {str(e)}")
        except Exception as e:
            self.logger.log_error(f"OpenAI请求错误: {str(e)}")
            raise APIConnectionError(f"请求错误: {str(e)}")

class CurlClient(APIClient):
    """CURL风格API客户端"""
    
    def __init__(self, logger: Logger):
        self.logger = logger
    
    def send_request(self, config: APIConfig, messages: List[Dict], tools: Optional[List[Dict]] = None) -> str:
        """发送CURL API请求"""
        full_response = ""
        self.logger.log_info(f"发送CURL请求到 {config.api_base} (模型: {config.model})")
        
        try:
            payload = {
                "model": config.model,
                "messages": messages
            }
            
            # 添加工具调用参数
            if tools:
                payload["tools"] = tools
                self.logger.log_info(f"启用工具调用，工具数量: {len(tools)}")
            
            if config.use_non_streaming_response:
                payload["stream"] = False
            else:
                payload["stream"] = True
            
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
                stream=not config.use_non_streaming_response
            )
            
            if response.status_code != 200:
                self.logger.log_error(f"API响应错误: HTTP {response.status_code}")
                raise APIResponseError(f"HTTP {response.status_code}")
            
            if config.use_non_streaming_response:
                return self._handle_non_streaming_response(response, config)
            else:
                return self._handle_streaming_response(response, config)
        
        except requests.exceptions.RequestException as e:
            self.logger.log_error(f"网络错误: {str(e)}")
            raise APIConnectionError(f"网络错误: {str(e)}")
        except Exception as e:
            self.logger.log_error(f"请求错误: {str(e)}")
            raise APIConnectionError(f"请求错误: {str(e)}")
    
    def _handle_non_streaming_response(self, response, config: APIConfig) -> str:
        """处理非流式响应"""
        data = response.json()
        
        if "choices" in data and len(data["choices"]) > 0:
            choice = data["choices"][0]
            if "message" in choice and "content" in choice["message"]:
                content = choice["message"]["content"]
                self.logger.log_ai_output(config, content, is_streaming=False)
                return content
        
        self.logger.log_error("AI未返回有效响应")
        raise APIResponseError("AI未返回有效响应")
    
    def _handle_streaming_response(self, response, config: APIConfig) -> str:
        """处理流式响应"""
        full_response = ""
        
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
                        self.logger.log_ai_output(config, content)
                
                if data.get("done", False) or data.get("finish_reason", None):
                    break
                    
            except json.JSONDecodeError:
                continue
        
        if not full_response:
            self.logger.log_error("AI未返回有效响应")
            raise APIResponseError("AI未返回有效响应")
        
        return full_response

# ==================== 会话管理 ====================
class DefaultSessionManager(SessionManager):
    """默认会话管理器"""
    
    def __init__(self, history_dir: str = "chat_history", logger: Optional[Logger] = None):
        self.history_dir = Path(history_dir)
        self.history_dir.mkdir(exist_ok=True)
        self.logger = logger
    
    def save_session(self, messages: List[Dict], filename: str) -> Path:
        """保存会话到文件"""
        if not filename.endswith('.json'):
            filename += '.json'
        
        file_path = self.history_dir / filename
        
        title = "未命名对话"
        for msg in messages:
            if msg['role'] == 'user':
                title = msg['content'].replace('\n', ' ')[:20] + "..."
                break
        
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
            
            if self.logger:
                self.logger.log_info(f"会话已保存到: {file_path}")
                
            return file_path
        except Exception as e:
            if self.logger:
                self.logger.log_error(f"保存失败: {str(e)}")
            raise RuntimeError(f"保存失败: {str(e)}")
    
    def load_session(self, filename: str) -> List[Dict]:
        """从文件加载会话"""
        if not filename.endswith('.json'):
            filename += '.json'
        
        file_path = self.history_dir / filename
        
        if not file_path.exists():
            if self.logger:
                self.logger.log_error(f"历史文件不存在: {file_path}")
            raise FileNotFoundError(f"历史文件不存在: {file_path}")
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            if self.logger:
                self.logger.log_info(f"成功加载会话: {file_path}")
                
            return data['messages']
        except Exception as e:
            if self.logger:
                self.logger.log_error(f"加载失败: {str(e)}")
            raise InvalidSessionError(f"加载失败: {str(e)}")

# ==================== 日志记录 ====================
class AILogger(Logger):
    """AI输出日志记录器"""
    
    def __init__(self, log_dir: str = "logs"):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(exist_ok=True)
        
        # 主日志文件
        self.log_file = self.log_dir / "chat_core.log"
        self.ai_output_log = self.log_dir / "AIoutput.log"
        
        # 配置日志
        logging.basicConfig(
            filename=self.log_file,
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        self.logger = logging.getLogger("chat_core")
    
    def _write_ai_log(self, entry: Dict) -> None:
        """写入AI输出日志"""
        try:
            timestamp = entry.get("timestamp", "")
            model = entry.get("model", "")
            provider = entry.get("provider", "")
            content = entry.get("content", "")
            streaming = "true" if entry.get("streaming", False) else "false"
            
            content = content.replace('\n', '\\n').replace('\r', '\\r')
            log_line = f"{timestamp} - {model} - {provider} - {content} - {streaming}\n"
            
            with open(self.ai_output_log, 'a', encoding='utf-8') as log_file:
                fcntl.flock(log_file, fcntl.LOCK_EX)
                log_file.write(log_line)
                log_file.flush()
                fcntl.flock(log_file, fcntl.LOCK_UN)
                
        except Exception as e:
            self.log_error(f"写入AI输出日志失败: {str(e)}")
    
    def log_ai_output(self, config: APIConfig, content: str, is_streaming: bool = True) -> None:
        """记录AI输出"""
        try:
            if content:
                timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
                entry = {
                    "timestamp": timestamp,
                    "model": config.model,
                    "provider": config.name,
                    "content": content,
                    "streaming": is_streaming
                }
                self._write_ai_log(entry)
        except Exception as e:
            self.log_error(f"创建AI日志条目失败: {str(e)}")
    
    def log_error(self, message: str, exception: Optional[Exception] = None) -> None:
        """记录错误日志"""
        if exception:
            self.logger.error(f"{message}: {str(exception)}")
        else:
            self.logger.error(message)
    
    def log_info(self, message: str) -> None:
        """记录信息日志"""
        self.logger.info(message)

# ==================== 核心聊天类 ====================
class ChatCore:
    """重构后的简洁聊天核心类"""
    
    def __init__(self, config_filename: Optional[str] = None, 
                 config_manager: Optional[ConfigManager] = None,
                 file_processor: Optional[FileProcessor] = None,
                 session_manager: Optional[SessionManager] = None,
                 logger: Optional[Logger] = None):
        
        # 依赖注入或默认实现
        self.logger = logger or AILogger()
        self.config_manager = config_manager or JSONConfigManager(self.logger)
        self.file_processor = file_processor or DefaultFileProcessor(logger=self.logger)
        self.session_manager = session_manager or DefaultSessionManager(logger=self.logger)
        
        # API客户端工厂
        self.api_clients: Dict[str, APIClient] = {
            "openai": OpenAIClient(self.logger),
            "curl": CurlClient(self.logger)
        }
        
        # 工具回调（由主程序提供）
        self.tool_callbacks: Optional[ToolCallbacks] = None
        
        # 加载配置
        if config_filename:
            self.configs = self.config_manager.load_configs(config_filename)
        else:
            self.configs = []
    
    def set_tool_callbacks(self, tool_callbacks: ToolCallbacks) -> None:
        """设置工具回调函数（由主程序调用）"""
        self.tool_callbacks = tool_callbacks
        self.logger.log_info(f"设置工具回调，工具数量: {len(tool_callbacks.tool_schemas)}")
    
    def register_api_client(self, client_type: str, client: APIClient) -> None:
        """注册自定义API客户端"""
        self.api_clients[client_type] = client
        self.logger.log_info(f"注册API客户端: {client_type}")
    
    def run_chat_session(self, session: List[Dict], config_index: int = 0) -> Tuple[List[Dict], str]:
        """执行聊天会话"""
        if not session:
            raise ValueError("会话不能为空")
        
        if not self.configs:
            raise ValueError("没有可用的API配置")
        
        config = self.config_manager.get_config(config_index)
        self.logger.log_info(f"使用配置: {config.name} ({config.model})")
        
        # 处理文件嵌入
        processed_session = self._process_session_files(session)
        
        # 准备工具
        tools = None
        if self.tool_callbacks and self.tool_callbacks.tool_schemas:
            tools = self.tool_callbacks.tool_schemas
            self.logger.log_info(f"启用工具调用，可用工具: {len(tools)}")
        
        # 获取API客户端
        client = self.api_clients.get(config.request_type)
        if not client:
            raise ValueError(f"不支持的请求类型: {config.request_type}")
        
        # 发送请求
        try:
            full_response = client.send_request(config, processed_session, tools)
            
            # 添加元数据
            response_msg = {
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
            updated_session.append(response_msg)
            
            self.logger.log_info(f"聊天会话完成，回复长度: {len(full_response)} 字符")
            return updated_session, full_response
            
        except Exception as e:
            self.logger.log_error(f"API请求失败: {str(e)}")
            raise
    
    def _process_session_files(self, session: List[Dict]) -> List[Dict]:
        """处理会话中的文件嵌入"""
        processed_session = []
        
        for msg in session:
            if msg['role'] == 'user':
                try:
                    content = self.file_processor.process_file_embeddings(msg['content'])
                    processed_session.append({"role": "user", "content": content})
                except Exception as e:
                    self.logger.log_error(f"处理消息失败: {str(e)}")
                    raise
            else:
                processed_session.append(msg)
        
        return processed_session
    
    def save_session(self, messages: List[Dict], filename: str) -> Path:
        """保存会话（委托给SessionManager）"""
        return self.session_manager.save_session(messages, filename)
    
    def load_session(self, filename: str) -> List[Dict]:
        """加载会话（委托给SessionManager）"""
        return self.session_manager.load_session(filename)
    
    def list_configs(self) -> List[APIConfig]:
        """列出所有配置"""
        return self.config_manager.list_configs()
