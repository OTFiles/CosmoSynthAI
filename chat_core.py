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

# 异常定义
class FileTooLargeError(Exception): pass
class APIConnectionError(Exception): pass
class APIResponseError(Exception): pass
class InvalidSessionError(Exception): pass
class ConfigLoadError(Exception): pass

class ChatCore:
    # 类常量
    MAX_FILE_SIZE = 1024 * 1024  # 最大文件大小限制（1MB）
    MAX_MESSAGE_LENGTH = 5000    # 最大消息长度限制
    
    def __init__(self, config_filename=None, log_dir="logs", history_dir="chat_history"):
        """
        初始化ChatCore
        
        参数:
            config_filename: 配置文件路径
            log_dir: 日志目录
            history_dir: 历史记录目录
        """
        # 设置日志目录
        self.LOG_DIR = Path(log_dir)
        self.LOG_DIR.mkdir(exist_ok=True)
        self.LOG_FILE = self.LOG_DIR / "chat_core.log"
        self.AI_OUTPUT_LOG = self.LOG_DIR / "AIoutput.log"  # AI输出日志文件
        
        # 配置日志
        logging.basicConfig(
            filename=self.LOG_FILE,
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        self.logger = logging.getLogger("chat_core")
        
        # 历史记录目录
        self.HISTORY_DIR = Path(history_dir)
        self.HISTORY_DIR.mkdir(exist_ok=True)
        
        # 加载配置
        self.configs = []
        if config_filename:
            self.configs = self.load_configs(config_filename)
            
        # 检测OpenAI版本
        self.openai_version = self._get_openai_version()
        self.logger.info(f"检测到OpenAI版本: {self.openai_version}")
    
    def _get_openai_version(self):
        """获取OpenAI库版本"""
        try:
            return openai.__version__
        except AttributeError:
            # 旧版本可能没有__version__属性
            return "0.28.1"  # 假设是旧版本
    
    def _write_ai_log(self, entry):
        """
        将AI输出条目写入日志文件
        新格式: timestamp - model - provider - content - streaming
        """
        try:
            # 确保日志目录存在
            self.AI_OUTPUT_LOG.parent.mkdir(parents=True, exist_ok=True)
            
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
            
            with open(self.AI_OUTPUT_LOG, 'a', encoding='utf-8') as log_file:
                # 获取文件锁
                fcntl.flock(log_file, fcntl.LOCK_EX)
                
                # 写入格式化的日志行
                log_file.write(log_line)
                log_file.flush()  # 强制刷新缓冲区
                
                # 释放文件锁
                fcntl.flock(log_file, fcntl.LOCK_UN)
                
        except IOError as e:
            self.logger.error(f"写入AI输出日志失败: {str(e)}")
        except Exception as e:
            self.logger.error(f"AI日志写入意外错误: {str(e)}")

    def _log_ai_output(self, config, content, is_streaming=True):
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
                self._write_ai_log(entry)
            
        except Exception as e:
            self.logger.error(f"创建AI日志条目失败: {str(e)}")

    class ChatConfig:
        def __init__(self, name, api_base, api_key, model, request_type="openai", headers=None, use_non_streaming_response=False):
            self.name = name
            self.api_base = api_base
            self.api_key = api_key
            self.model = model
            self.request_type = request_type
            self.headers = headers or {}
            self.use_non_streaming_response = use_non_streaming_response
        
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
                "use_non_streaming_response": self.use_non_streaming_response
            }

    def load_configs(self, config_filename):
        """从JSON配置文件加载所有配置"""
        configs = []
        
        if not os.path.exists(config_filename):
            self.logger.error(f"配置文件不存在: {config_filename}")
            raise FileNotFoundError(f"配置文件不存在: {config_filename}")
        
        try:
            self.logger.info(f"开始加载配置文件: {config_filename}")
            with open(config_filename, 'r', encoding='utf-8') as f:
                config_data = json.load(f)
            
            # 验证配置文件版本
            if "version" not in config_data:
                self.logger.warning("配置文件缺少版本字段，假设为版本1.0")
            
            # 检查配置数组
            if "configs" not in config_data:
                self.logger.error("配置文件缺少'configs'数组")
                raise ConfigLoadError("配置文件缺少'configs'数组")
            
            # 加载每个配置
            for config_obj in config_data["configs"]:
                # 验证必需字段
                required_fields = ["name", "api_base", "api_key", "model"]
                for field in required_fields:
                    if field not in config_obj:
                        self.logger.error(f"配置缺少必需字段: {field}")
                        raise ConfigLoadError(f"配置缺少必需字段: {field}")
                
                # 提取字段
                name = config_obj["name"]
                api_base = config_obj["api_base"]
                api_key = config_obj["api_key"]
                model = config_obj["model"]
                
                # 可选字段
                request_type = config_obj.get("request_type", "openai")
                if request_type not in ["openai", "curl"]:
                    request_type = "openai"
                
                headers = config_obj.get("headers", {})
                use_non_streaming_response = config_obj.get("use_non_streaming_response", False)
                
                # 创建配置对象
                config = self.ChatConfig(
                    name, api_base, api_key, model, 
                    request_type, headers, use_non_streaming_response
                )
                configs.append(config)
                self.logger.info(f"加载配置: {config}")
                
        except json.JSONDecodeError as e:
            self.logger.error(f"JSON解析错误: {str(e)}")
            raise ConfigLoadError(f"JSON解析错误: {str(e)}")
        except Exception as e:
            self.logger.error(f"加载配置文件出错: {str(e)}")
            raise ConfigLoadError(f"加载配置文件出错: {str(e)}")
        
        if not configs:
            self.logger.error("配置文件中未找到有效的API配置")
            raise ConfigLoadError("配置文件中未找到有效的API配置")
        
        self.logger.info(f"成功加载 {len(configs)} 个API配置")
        return configs

    def attach_file(self, content, file_path):
        """将文件内容嵌入到字符串中"""
        self.logger.info(f"尝试嵌入文件: {file_path}")
        
        if not os.path.exists(file_path):
            self.logger.error(f"文件不存在: {file_path}")
            raise FileNotFoundError(f"文件不存在: {file_path}")
        
        file_size = os.path.getsize(file_path)
        if file_size > self.MAX_FILE_SIZE:
            self.logger.error(f"文件过大(>{self.MAX_FILE_SIZE/1024}KB): {file_path}")
            raise FileTooLargeError(f"文件过大(>{self.MAX_FILE_SIZE/1024}KB): {file_path}")
        
        with open(file_path, 'r', encoding='utf-8') as f:
            file_content = f.read()
        
        self.logger.info(f"成功嵌入文件内容: {file_path} ({len(file_content)} 字符)")
        return content.replace(f"{{{{:F{file_path}}}}}", f"\n```文件内容:{file_path}\n{file_content}\n```\n")

    def save_session(self, messages, filename):
        """保存对话历史到文件"""
        self.logger.info(f"尝试保存会话到文件: {filename}")
        
        if not filename.endswith('.json'):
            filename += '.json'
        
        file_path = self.HISTORY_DIR / filename
        
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
            self.logger.info(f"会话已保存到: {file_path}")
            return file_path
        except Exception as e:
            self.logger.error(f"保存失败: {str(e)}")
            raise RuntimeError(f"保存失败: {str(e)}")

    def load_session(self, filename):
        """从文件加载对话历史"""
        self.logger.info(f"尝试加载会话文件: {filename}")
        
        if not filename.endswith('.json'):
            filename += '.json'
        
        file_path = self.HISTORY_DIR / filename
        
        if not file_path.exists():
            self.logger.error(f"历史文件不存在: {file_path}")
            raise FileNotFoundError(f"历史文件不存在: {file_path}")
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            self.logger.info(f"成功加载会话: {file_path} ({len(data['messages'])} 条消息)")
            return data['messages']
        except Exception as e:
            self.logger.error(f"加载失败: {str(e)}")
            raise InvalidSessionError(f"加载失败: {str(e)}")

    def _embed_file_content(self, content):
        """内部函数：替换输入字符串中的文件标记为文件内容"""
        pattern = r'\{\{:F([^}]+)\}\}'
        matches = re.findall(pattern, content)
        
        for file_path in matches:
            file_path = file_path.strip()
            try:
                self.logger.debug(f"处理文件标记: {file_path}")
                
                if not os.path.exists(file_path):
                    raise FileNotFoundError(f"文件不存在: {file_path}")
                
                file_size = os.path.getsize(file_path)
                if file_size > self.MAX_FILE_SIZE:
                    raise FileTooLargeError(f"文件过大(>{self.MAX_FILE_SIZE/1024}KB): {file_path}")
                
                with open(file_path, 'r', encoding='utf-8') as f:
                    file_content = f.read()
                
                content = content.replace(f"{{{{:F{file_path}}}}}", f"\n```文件内容:{file_path}\n{file_content}\n```\n")
                self.logger.debug(f"文件内容嵌入成功: {file_path}")
            except Exception as e:
                self.logger.error(f"文件内容嵌入失败: {file_path} - {str(e)}")
                raise e
        
        return content

    def _send_openai_request(self, config, messages):
        """发送OpenAI API请求（兼容不同版本）"""
        full_response = ""
        self.logger.info(f"发送OpenAI请求到 {config.api_base} (模型: {config.model})")
        
        try:
            # 根据版本使用不同的API调用方式
            if self.openai_version.startswith("0."):
                # 旧版本OpenAI库 (0.28.1)
                openai.api_base = config.api_base
                openai.api_key = config.api_key
                
                # 创建流式响应
                response = openai.ChatCompletion.create(
                    model=config.model,
                    messages=messages,
                    stream=True,
                    headers=config.headers
                )
                
                self.logger.debug("开始接收流式响应...")
                
                # 处理流式响应
                for chunk in response:
                    if "choices" in chunk and len(chunk["choices"]) > 0:
                        delta = chunk["choices"][0].get("delta", {})
                        if "content" in delta and delta["content"]:
                            content = delta["content"]
                            full_response += content
                            
                            # 记录流式输出到AI日志
                            try:
                                self._log_ai_output(config, content)
                            except Exception as e:
                                self.logger.error(f"记录流式输出失败: {str(e)}")
            else:
                # 新版本OpenAI库 (1.x)
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
                
                self.logger.debug("开始接收流式响应...")
                
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
                            self._log_ai_output(config, content)
                        except Exception as e:
                            self.logger.error(f"记录流式输出失败: {str(e)}")
            
            if not full_response:
                self.logger.warning("AI未返回有效响应")
                raise APIResponseError("AI未返回有效响应")
            
            self.logger.info(f"成功接收响应: {len(full_response)} 字符")
            return full_response
        
        except openai.APIError as e:
            self.logger.error(f"OpenAI API错误: {str(e)}")
            raise APIConnectionError(f"API错误: {str(e)}")
        except openai.APIConnectionError as e:
            self.logger.error(f"OpenAI连接错误: {str(e)}")
            raise APIConnectionError(f"连接错误: {str(e)}")
        except Exception as e:
            self.logger.error(f"OpenAI请求错误: {str(e)}")
            raise APIConnectionError(f"请求错误: {str(e)}")

    def _send_curl_request(self, config, messages):
        """使用Requests库发送自定义请求"""
        full_response = ""
        self.logger.info(f"发送CURL请求到 {config.api_base} (模型: {config.model})")
        
        try:
            if config.use_non_streaming_response:
                payload = {"model": config.model, "messages": messages}
            else:
                payload = {"model": config.model, "messages": messages, "stream": True}
            
            headers = {
                "Authorization": f"Bearer {config.api_key}",
                "Content-Type": "application/json"
            }
            
            if config.headers:
                headers.update(config.headers)
            
            self.logger.debug(f"请求头: {headers}")
            self.logger.debug(f"请求体: {json.dumps(payload, ensure_ascii=False)[:500]}...")
            
            response = requests.post(
                config.api_base,
                json=payload,
                headers=headers,
                stream=not config.use_non_streaming_response
            )
            
            if response.status_code != 200:
                self.logger.error(f"API响应错误: HTTP {response.status_code} - {response.text[:500]}")
                raise APIResponseError(f"HTTP {response.status_code} - {response.text}")
            
            # 处理非流式响应
            if config.use_non_streaming_response:
                self.logger.debug("处理非流式响应")
                data = response.json()
                if "choices" in data and len(data["choices"]) > 0:
                    choice = data["choices"][0]
                    if "message" in choice and "content" in choice["message"]:
                        full_response = choice["message"]["content"]
                    else:
                        self.logger.warning("API响应格式不兼容")
                        raise APIResponseError("API响应格式不兼容")
                else:
                    self.logger.warning("AI未返回有效响应")
                    raise APIResponseError("AI未返回有效响应")
                
                # 记录完整响应到AI日志
                try:
                    self._log_ai_output(config, full_response, is_streaming=False)
                except Exception as e:
                    self.logger.error(f"记录完整响应失败: {str(e)}")
                    
                return full_response
            
            # 处理流式响应
            self.logger.debug("开始处理流式响应...")
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
                                self._log_ai_output(config, content)
                            except Exception as e:
                                self.logger.error(f"记录流式输出失败: {str(e)}")
                    
                    if data.get("done", False) or data.get("finish_reason", None):
                        break
                        
                except json.JSONDecodeError:
                    continue
                except Exception as e:
                    self.logger.error(f"响应解析错误: {str(e)}")
                    raise APIResponseError(f"解析错误: {str(e)}")
            
            if not full_response:
                self.logger.warning("AI未返回有效响应")
                raise APIResponseError("AI未返回有效响应")
            
            self.logger.info(f"成功接收响应: {len(full_response)} 字符")
            return full_response
        
        except requests.exceptions.RequestException as e:
            self.logger.error(f"网络错误: {str(e)}")
            raise APIConnectionError(f"网络错误: {str(e)}")
        except Exception as e:
            self.logger.error(f"请求错误: {str(e)}")
            raise APIConnectionError(f"请求错误: {str(e)}")

    def run_chat_session(self, session, config_index=0):
        """
        执行聊天会话并返回AI回复
        
        参数:
            session: 聊天会话消息列表
            config_index: 要使用的API配置索引
            
        返回:
            (完整会话, AI回复文本)
        """
        self.logger.info("开始聊天会话")
        
        if not session:
            self.logger.error("会话不能为空")
            raise ValueError("会话不能为空")
        
        if not self.configs:
            self.logger.error("没有可用的API配置")
            raise ValueError("没有可用的API配置")
        
        if config_index < 0 or config_index >= len(self.configs):
            self.logger.error(f"无效的配置索引: {config_index} (可用配置数: {len(self.configs)})")
            raise IndexError(f"无效的配置索引: {config_index} (可用配置数: {len(self.configs)})")
        
        config = self.configs[config_index]
        self.logger.info(f"使用配置: {config.name} ({config.model}), 请求类型: {config.request_type}")
        
        # 处理文件标记
        processed_session = []
        for msg in session:
            if msg['role'] == 'user':
                try:
                    self.logger.debug(f"处理用户消息: {msg['content'][:100]}...")
                    # 处理文件标记
                    content = self._embed_file_content(msg['content'])
                    # 截断过长的消息
                    if len(content) > self.MAX_MESSAGE_LENGTH:
                        self.logger.warning(f"消息过长({len(content)}字符)，已截断")
                        content = content[:self.MAX_MESSAGE_LENGTH] + "\n...（消息过长，已截断）"
                    processed_session.append({"role": "user", "content": content})
                except Exception as e:
                    self.logger.error(f"处理消息失败: {str(e)}")
                    raise e
            else:
                processed_session.append(msg)
        
        # 发送所有消息（不再限制数量）
        messages_to_send = processed_session
        self.logger.info(f"发送 {len(messages_to_send)} 条消息到API")
        
        # 发送请求
        try:
            if config.request_type == "curl":
                full_response = self._send_curl_request(config, messages_to_send)
            else:
                full_response = self._send_openai_request(config, messages_to_send)
        except Exception as e:
            self.logger.error(f"API请求失败: {str(e)}")
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
        
        self.logger.info(f"聊天会话完成，回复长度: {len(full_response)} 字符")
        return updated_session, full_response