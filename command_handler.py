# command_handler.py
import re
import json
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass
from logging_system import UnifiedLogger
from configuration_manager import ConfigurationManager, AINotFoundError

@dataclass
class CommandResult:
    """命令执行结果"""
    success: bool
    message: str
    requires_followup: bool = False
    followup_ai: Optional[str] = None

class CommandHandler:
    """命令处理器，负责特殊命令和权限管理"""
    
    def __init__(self, config_manager: ConfigurationManager, logger: UnifiedLogger):
        self.config_manager = config_manager
        self.logger = logger
        
        # 注册命令处理器
        self.command_handlers = {
            "call": self._handle_call_command,
            "channel_list": self._handle_channel_list,
            "set_permissions": self._handle_set_permissions,
            "add_to_channel": self._handle_add_to_channel,
            "remove_from_channel": self._handle_remove_from_channel,
            "reset_memory": self._handle_reset_memory,
        }
    
    def process_command(self, speaker_id: str, message: str) -> Optional[CommandResult]:
        """处理特殊命令"""
        # 呼叫命令 {{Call:AI名称}}
        call_match = re.search(r"\{\{Call:([^\}]+)\}\}", message)
        if call_match and speaker_id in self.config_manager.system_config.allowed_callers:
            return self._handle_call_command(speaker_id, call_match.group(1).strip())
        
        # 频道管理命令
        channel_commands = [
            (r"\{\{pd\.l\(([^\)]+)\)\}\}", "channel_list"),
            (r"\{\{pd\.s\(([^,]+),([^,]+),([^\)]+)\)\}\}", "set_permissions"),
            (r"\{\{pd\.a\(([^,]+),([^\)]+)\)\}\}", "add_to_channel"),
            (r"\{\{pd\.d\(([^,]+),([^\)]+)\)\}\}", "remove_from_channel"),
        ]
        
        for pattern, command_type in channel_commands:
            match = re.search(pattern, message)
            if match and speaker_id == self.config_manager.system_config.channel_manager_ai:
                return self.command_handlers[command_type](speaker_id, *match.groups())
        
        # 记忆管理命令
        memory_match = re.search(r"\{\{ep\.r\(([^,]+),([^\)]+)\)\}\}", message)
        if memory_match and speaker_id == self.config_manager.system_config.memory_manager_ai:
            return self._handle_reset_memory(
                speaker_id, 
                memory_match.group(1).strip(),
                memory_match.group(2).strip().lower() == "true"
            )
        
        return None
    
    def _handle_call_command(self, speaker_id: str, called_ai: str) -> CommandResult:
        """处理呼叫命令"""
        if called_ai not in self.config_manager.ai_configs:
            return CommandResult(False, f"找不到AI '{called_ai}'")
        
        # 添加到优先级队列的逻辑在Orchestrator中处理
        # 这里只返回需要后续处理的信息
        return CommandResult(
            success=True,
            message=f"成功呼叫 {called_ai}",
            requires_followup=True,
            followup_ai=called_ai
        )
    
    def _handle_channel_list(self, speaker_id: str, channel_name: str) -> CommandResult:
        """处理列出频道成员命令"""
        if channel_name not in self._get_all_channels():
            return CommandResult(False, f"频道 '{channel_name}' 不存在")
        
        members = []
        for ai_id, ai_config in self.config_manager.ai_configs.items():
            if channel_name in ai_config.channels:
                permissions = ai_config.channels[channel_name]
                members.append(f"{ai_id}: {permissions}")
        
        result = f"频道 '{channel_name}' 成员:\n" + "\n".join(members) if members else f"频道 '{channel_name}' 无成员"
        
        self.logger.log_command(speaker_id, f"列出频道 {channel_name}", "成功")
        return CommandResult(True, result)
    
    def _handle_set_permissions(self, speaker_id: str, channel_name: str, 
                              ai_name: str, permissions_str: str) -> CommandResult:
        """处理设置权限命令"""
        try:
            permissions = json.loads(permissions_str)
        except json.JSONDecodeError:
            return CommandResult(False, "权限格式无效，必须是JSON数组")
        
        if channel_name not in self._get_all_channels():
            return CommandResult(False, f"频道 '{channel_name}' 不存在")
        
        if ai_name not in self.config_manager.ai_configs:
            return CommandResult(False, f"AI '{ai_name}' 未定义")
        
        # 验证权限格式
        if not isinstance(permissions, list) or not all(isinstance(p, str) for p in permissions):
            return CommandResult(False, "权限必须为字符串列表")
        
        # 检查权限值是否有效
        valid_perms = ["receive", "send"]
        for perm in permissions:
            if perm not in valid_perms:
                return CommandResult(False, f"无效权限: '{perm}'，有效值为 {valid_perms}")
        
        # 更新权限
        self.config_manager.ai_configs[ai_name].channels[channel_name] = permissions
        
        self.logger.log_command(
            speaker_id, 
            f"设置 {ai_name} 在 {channel_name} 的权限", 
            f"成功: {permissions}"
        )
        return CommandResult(True, f"成功设置 {ai_name} 在 '{channel_name}' 的权限为: {permissions}")
    
    def _handle_add_to_channel(self, speaker_id: str, channel_name: str, 
                             ai_name: str) -> CommandResult:
        """处理添加AI到频道命令"""
        if channel_name not in self._get_all_channels():
            return CommandResult(False, f"频道 '{channel_name}' 不存在")
        
        if ai_name not in self.config_manager.ai_configs:
            return CommandResult(False, f"AI '{ai_name}' 未定义")
        
        if channel_name in self.config_manager.ai_configs[ai_name].channels:
            return CommandResult(False, f"{ai_name} 已在频道 '{channel_name}' 中")
        
        # 添加AI到频道（默认只接收）
        self.config_manager.ai_configs[ai_name].channels[channel_name] = ["receive"]
        
        self.logger.log_command(
            speaker_id, 
            f"添加 {ai_name} 到频道 {channel_name}", 
            "成功"
        )
        return CommandResult(True, f"成功添加 {ai_name} 到频道 '{channel_name}'")
    
    def _handle_remove_from_channel(self, speaker_id: str, channel_name: str, 
                                  ai_name: str) -> CommandResult:
        """处理从频道移除AI命令"""
        if channel_name not in self._get_all_channels():
            return CommandResult(False, f"频道 '{channel_name}' 不存在")
        
        if ai_name not in self.config_manager.ai_configs:
            return CommandResult(False, f"AI '{ai_name}' 未定义")
        
        if channel_name not in self.config_manager.ai_configs[ai_name].channels:
            return CommandResult(False, f"{ai_name} 不在频道 '{channel_name}' 中")
        
        # 从频道移除AI
        del self.config_manager.ai_configs[ai_name].channels[channel_name]
        
        self.logger.log_command(
            speaker_id, 
            f"从频道 {channel_name} 移除 {ai_name}", 
            "成功"
        )
        return CommandResult(True, f"成功从频道 '{channel_name}' 移除 {ai_name}")
    
    def _handle_reset_memory(self, speaker_id: str, ai_name: str, 
                           use_history: bool) -> CommandResult:
        """处理重置记忆命令"""
        if ai_name not in self.config_manager.ai_configs:
            return CommandResult(False, f"AI '{ai_name}' 未定义")
        
        # 记忆重置逻辑在Orchestrator中处理
        # 这里只返回成功信息
        self.logger.log_command(
            speaker_id, 
            f"重置 {ai_name} 的记忆", 
            f"使用历史: {use_history}"
        )
        return CommandResult(
            success=True,
            message=f"成功重置 {ai_name} 的记忆 (参考历史: {use_history})",
            requires_followup=True,
            followup_ai=ai_name
        )
    
    def _get_all_channels(self) -> List[str]:
        """获取所有频道列表"""
        channels = set()
        for ai_config in self.config_manager.ai_configs.values():
            channels.update(ai_config.channels.keys())
        return list(channels)

class ChannelNotFoundError(Exception):
    """频道未找到异常"""
    pass

class PermissionError(Exception):
    """权限异常"""
    pass

class InvalidCommandError(Exception):
    """无效命令异常"""
    pass