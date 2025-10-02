# message_processor.py
import re
from typing import List, Tuple, Dict, Any, Optional
from dataclasses import dataclass
from logging_system import UnifiedLogger, LogType
from configuration_manager import AIConfig, ConfigurationManager

@dataclass
class ParsedMessage:
    """解析后的消息"""
    channels: List[str]
    content: str
    system_messages: List[str]

class MessageProcessor:
    """消息处理器，负责消息解析、监察和分发"""
    
    def __init__(self, config_manager: ConfigurationManager, logger: UnifiedLogger):
        self.config_manager = config_manager
        self.logger = logger
    
    def parse_message(self, message: str, speaker_id: str) -> ParsedMessage:
        """解析AI的消息格式"""
        if not isinstance(message, str):
            self.logger.error(f"消息不是字符串类型: {type(message)}", ai_id=speaker_id)
            message = str(message)
        
        # 移除<think>标签及其内容
        message = self._remove_think_tags(message)
        
        # 提取系统消息
        system_messages, cleaned_message = self._extract_system_messages(message)
        
        # 解析频道和内容
        channels, content = self._parse_channels_and_content(cleaned_message, speaker_id)
        
        return ParsedMessage(
            channels=channels,
            content=content,
            system_messages=system_messages
        )
    
    def _remove_think_tags(self, text: str) -> str:
        """移除<think>和<think/>包裹的内容"""
        return re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    
    def _extract_system_messages(self, message: str) -> Tuple[List[str], str]:
        """提取消息中的系统消息标签"""
        system_messages = []
        matches = re.findall(r"<system>(.*?)<system/>", message, re.DOTALL)
        for match in matches:
            system_messages.append(match.strip())
        
        cleaned_message = re.sub(r"<system>.*?<system/>", "", message, flags=re.DOTALL).strip()
        return system_messages, cleaned_message
    
    def _parse_channels_and_content(self, message: str, speaker_id: str) -> Tuple[List[str], str]:
        """解析频道和内容"""
        ai_config = self.config_manager.get_ai_config(speaker_id)
        
        # 格式2: [频道1][频道2]消息(需要优先处理)
        multi_match = re.match(r"^(\[[^\]]+\])+(.+)$", message)
        if multi_match:
            channels = re.findall(r"\[([^\]]+)\]", message)
            content = multi_match.group(2)
            valid_channels = self._validate_channels(channels, ai_config)
            return valid_channels, content
        
        # 格式1: [频道]消息
        single_match = re.match(r"^\[([^\]]+)\](.+)$", message)
        if single_match:
            channel = single_match.group(1)
            content = single_match.group(2)
            valid_channels = self._validate_channels([channel], ai_config)
            return valid_channels, content
        
        # 默认: 广播到所有有权限的频道
        broadcast_channels = []
        for channel, perms in ai_config.channels.items():
            if "send" in perms:
                broadcast_channels.append(channel)
        
        if not broadcast_channels:
            raise InvalidMessageFormat(f"{speaker_id} 没有在任何频道拥有发送权限")
        
        return broadcast_channels, message
    
    def _validate_channels(self, channels: List[str], ai_config: AIConfig) -> List[str]:
        """验证频道权限"""
        valid_channels = []
        for channel in channels:
            if channel in ai_config.channels and "send" in ai_config.channels[channel]:
                valid_channels.append(channel)
            else:
                self.logger.warning(
                    f"在频道 '{channel}' 没有发送权限", 
                    ai_id=ai_config.ai_id,
                    metadata={"channel": channel, "permissions": ai_config.channels.get(channel)}
                )
        return valid_channels
    
    def monitor_message(self, speaker_id: str, message: str, 
                       chat_core: Any) -> bool:
        """监察消息是否合规"""
        ai_config = self.config_manager.get_ai_config(speaker_id)
        monitor_id = ai_config.monitor
        
        if not monitor_id or monitor_id not in self.config_manager.ai_configs:
            return True  # 没有监察AI或监察AI不存在，自动通过
        
        try:
            # 这里需要chat_core来运行监察会话
            # 由于chat_core的依赖，这里保持接口但实际实现在Orchestrator中协调
            monitor_config = self.config_manager.get_ai_config(monitor_id)
            
            # 准备监察会话
            session = [
                {"role": "system", "content": monitor_config.prompt},
                {
                    "role": "user", 
                    "content": f"请审查以下来自 {speaker_id} 的消息：\n\n{message}\n\n"
                    "请判断是否应驳回。如果驳回，请使用以下格式：\n"
                    "<reject>您的驳回理由<reject/>"
                }
            ]
            
            # 获取监察结果
            _, response = chat_core.run_chat_session(session, monitor_config.api_index)
            
            if not isinstance(response, str):
                self.logger.error(f"监察响应不是字符串类型: {type(response)}", ai_id=monitor_id)
                response = str(response)
            
            # 检查结果 - 使用标签格式
            reject_match = re.search(r"<reject>(.*?)<reject/>", response, re.DOTALL)
            if reject_match:
                reason = reject_match.group(1).strip()
                self.logger.log_rejection(speaker_id, message, reason)
                return False
            return True
        
        except Exception as e:
            self.logger.error(f"监察过程中出错: {str(e)}", ai_id=monitor_id)
            return True  # 出错时默认通过

class InvalidMessageFormat(Exception):
    """消息格式异常"""
    pass