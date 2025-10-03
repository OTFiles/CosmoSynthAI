import time
import random
import json
from collections import deque
from typing import Dict, List, Optional, Deque, Tuple, Any, Callable
from dataclasses import dataclass
from logging_system import UnifiedLogger, LogType
from configuration_manager import ConfigurationManager
from message_processor import MessageProcessor, ParsedMessage
from prompt_manager import PromptManager
from chat_core import ToolCallbacks, APIConnectionError

@dataclass
class PriorityTask:
    """优先级任务"""
    priority: str  # 'A' (最高) 或 'B'
    ai_id: str
    reason: str

class ChatOrchestrator:
    """聊天协调器，负责主循环和发言调度"""
    
    def __init__(self, config_manager: ConfigurationManager, 
                 message_processor: MessageProcessor,
                 prompt_manager: PromptManager,
                 logger: UnifiedLogger,
                 chat_core: Any):
        self.config_manager = config_manager
        self.message_processor = message_processor
        self.prompt_manager = prompt_manager
        self.logger = logger
        self.chat_core = chat_core
        
        # 状态管理
        self.ai_memories: Dict[str, List[Dict[str, str]]] = {}
        self.priority_queue: Deque[PriorityTask] = deque()
        self.round_count = 0
        self.last_speaker: Optional[str] = None
        self.first_ai_id: Optional[str] = None
        self.first_ai_spoken = False
        
        # 验证配置是否已加载
        if not hasattr(self.config_manager, 'ai_configs') or not self.config_manager.ai_configs:
            self.logger.error("配置管理器中的AI配置为空，无法初始化协调器")
            raise ValueError("AI配置为空，请先加载配置")
        
        # 初始化工具调用系统
        self._initialize_tool_system()
        
        # 在构造函数中初始化AI记忆
        self._initialize_ai_memories()
        
        # 记录初始化完成
        self.logger.info(f"聊天协调器初始化完成，共初始化 {len(self.ai_memories)} 个AI的记忆")
    
    def _initialize_tool_system(self) -> None:
        """初始化工具调用系统"""
        self.tool_callbacks = ToolCallbacks()
        
        # 注册工具函数
        self._register_tools()
        
        # 设置工具回调到ChatCore
        self.chat_core.set_tool_callbacks(self.tool_callbacks)
        
    def _register_tools(self) -> None:
        """注册所有可用的工具"""
        # 呼叫AI工具
        call_ai_schema = {
            "type": "function",
            "function": {
                "name": "call_ai",
                "description": "呼叫指定的AI，使其在下一轮优先发言",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "ai_name": {
                            "type": "string",
                            "description": "要呼叫的AI名称"
                        },
                        "reason": {
                            "type": "string", 
                            "description": "呼叫的原因"
                        }
                    },
                    "required": ["ai_name"]
                }
            }
        }
        
        # 频道管理工具
        channel_list_schema = {
            "type": "function",
            "function": {
                "name": "list_channel_members",
                "description": "列出指定频道的所有成员及其权限",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "channel_name": {
                            "type": "string",
                            "description": "频道名称"
                        }
                    },
                    "required": ["channel_name"]
                }
            }
        }
        
        set_permissions_schema = {
            "type": "function",
            "function": {
                "name": "set_channel_permissions",
                "description": "设置AI在指定频道的权限",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "channel_name": {
                            "type": "string",
                            "description": "频道名称"
                        },
                        "ai_name": {
                            "type": "string",
                            "description": "AI名称"
                        },
                        "permissions": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "权限列表，如['send', 'receive']"
                        }
                    },
                    "required": ["channel_name", "ai_name", "permissions"]
                }
            }
        }
        
        add_to_channel_schema = {
            "type": "function",
            "function": {
                "name": "add_ai_to_channel",
                "description": "将AI添加到指定频道",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "channel_name": {
                            "type": "string",
                            "description": "频道名称"
                        },
                        "ai_name": {
                            "type": "string",
                            "description": "要添加的AI名称"
                        }
                    },
                    "required": ["channel_name", "ai_name"]
                }
            }
        }
        
        remove_from_channel_schema = {
            "type": "function",
            "function": {
                "name": "remove_ai_from_channel",
                "description": "从指定频道移除AI",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "channel_name": {
                            "type": "string",
                            "description": "频道名称"
                        },
                        "ai_name": {
                            "type": "string",
                            "description": "要移除的AI名称"
                        }
                    },
                    "required": ["channel_name", "ai_name"]
                }
            }
        }
        
        # 记忆管理工具
        reset_memory_schema = {
            "type": "function",
            "function": {
                "name": "reset_ai_memory",
                "description": "重置指定AI的记忆",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "ai_name": {
                            "type": "string",
                            "description": "要重置记忆的AI名称"
                        },
                        "use_history": {
                            "type": "boolean",
                            "description": "是否参考历史记录"
                        }
                    },
                    "required": ["ai_name", "use_history"]
                }
            }
        }
        
        # 注册工具
        self.tool_callbacks.register_tool(call_ai_schema, self._tool_call_ai)
        self.tool_callbacks.register_tool(channel_list_schema, self._tool_list_channel_members)
        self.tool_callbacks.register_tool(set_permissions_schema, self._tool_set_permissions)
        self.tool_callbacks.register_tool(add_to_channel_schema, self._tool_add_to_channel)
        self.tool_callbacks.register_tool(remove_from_channel_schema, self._tool_remove_from_channel)
        self.tool_callbacks.register_tool(reset_memory_schema, self._tool_reset_memory)
        
        self.logger.info(f"已注册 {len(self.tool_callbacks.tool_schemas)} 个工具")
    
    def _tool_call_ai(self, ai_name: str, reason: str = "被呼叫") -> str:
        """工具：呼叫AI"""
        if ai_name not in self.config_manager.ai_configs:
            return f"错误：找不到AI '{ai_name}'"
        
        if ai_name in self.config_manager.system_config.excluded_ais:
            return f"错误：AI '{ai_name}' 在排除列表中，无法呼叫"
        
        # 添加到优先级队列
        self.add_priority_task(ai_name, reason, "B")
        return f"成功呼叫 {ai_name}，将在下次优先响应"
    
    def _tool_list_channel_members(self, channel_name: str) -> str:
        """工具：列出频道成员"""
        if channel_name not in self._get_all_channels():
            return f"错误：频道 '{channel_name}' 不存在"
        
        members = []
        for ai_id, ai_config in self.config_manager.ai_configs.items():
            if channel_name in ai_config.channels:
                permissions = ai_config.channels[channel_name]
                members.append(f"{ai_id}: {permissions}")
        
        result = f"频道 '{channel_name}' 成员:\n" + "\n".join(members) if members else f"频道 '{channel_name}' 无成员"
        return result
    
    def _tool_set_permissions(self, channel_name: str, ai_name: str, permissions: List[str]) -> str:
        """工具：设置频道权限"""
        if channel_name not in self._get_all_channels():
            return f"错误：频道 '{channel_name}' 不存在"
        
        if ai_name not in self.config_manager.ai_configs:
            return f"错误：AI '{ai_name}' 未定义"
        
        # 验证权限值是否有效
        valid_perms = ["receive", "send"]
        for perm in permissions:
            if perm not in valid_perms:
                return f"错误：无效权限 '{perm}'，有效值为 {valid_perms}"
        
        # 更新权限
        self.config_manager.ai_configs[ai_name].channels[channel_name] = permissions
        return f"成功设置 {ai_name} 在 '{channel_name}' 的权限为: {permissions}"
    
    def _tool_add_to_channel(self, channel_name: str, ai_name: str) -> str:
        """工具：添加AI到频道"""
        if channel_name not in self._get_all_channels():
            return f"错误：频道 '{channel_name}' 不存在"
        
        if ai_name not in self.config_manager.ai_configs:
            return f"错误：AI '{ai_name}' 未定义"
        
        if channel_name in self.config_manager.ai_configs[ai_name].channels:
            return f"错误：{ai_name} 已在频道 '{channel_name}' 中"
        
        # 添加AI到频道（默认只接收）
        self.config_manager.ai_configs[ai_name].channels[channel_name] = ["receive"]
        return f"成功添加 {ai_name} 到频道 '{channel_name}'"
    
    def _tool_remove_from_channel(self, channel_name: str, ai_name: str) -> str:
        """工具：从频道移除AI"""
        if channel_name not in self._get_all_channels():
            return f"错误：频道 '{channel_name}' 不存在"
        
        if ai_name not in self.config_manager.ai_configs:
            return f"错误：AI '{ai_name}' 未定义"
        
        if channel_name not in self.config_manager.ai_configs[ai_name].channels:
            return f"错误：{ai_name} 不在频道 '{channel_name}' 中"
        
        # 从频道移除AI
        del self.config_manager.ai_configs[ai_name].channels[channel_name]
        return f"成功从频道 '{channel_name}' 移除 {ai_name}"
    
    def _tool_reset_memory(self, ai_name: str, use_history: bool) -> str:
        """工具：重置AI记忆"""
        if ai_name not in self.config_manager.ai_configs:
            return f"错误：AI '{ai_name}' 未定义"
        
        # 获取原始提示词
        original_prompt = self.config_manager.ai_configs[ai_name].prompt
        
        # 如果使用历史记录
        if use_history:
            # 这里需要历史管理器，暂时简化处理
            new_system = f"{original_prompt}\n\n记忆已被重置（包含历史参考）"
        else:
            new_system = f"{original_prompt}\n\n记忆已被重置"
        
        # 重置记忆
        self.ai_memories[ai_name] = [{"role": "system", "content": new_system}]
        return f"成功重置 {ai_name} 的记忆 (参考历史: {use_history})"
    
    def _get_all_channels(self) -> List[str]:
        """获取所有频道列表"""
        channels = set()
        for ai_config in self.config_manager.ai_configs.values():
            channels.update(ai_config.channels.keys())
        return list(channels)

    def _initialize_ai_memories(self) -> None:
        """初始化AI记忆"""
        self.ai_memories.clear()
        if hasattr(self.config_manager, 'ai_configs') and self.config_manager.ai_configs:
            for ai_id, ai_config in self.config_manager.ai_configs.items():
                self.ai_memories[ai_id] = [{
                    "role": "system", 
                    "content": ai_config.prompt
                }]
            self.logger.info(f"成功初始化 {len(self.ai_memories)} 个AI的记忆")
        else:
            self.logger.error("配置管理器中的AI配置为空，无法初始化记忆")
            raise ValueError("AI配置为空，请先加载配置")

    def get_next_speaker(self) -> Optional[str]:
        """获取下一个发言的AI（考虑优先级队列）"""
        # 首先检查优先级队列
        if self.priority_queue:
            task = self.priority_queue.popleft()
            self.logger.info(
                f"优先级调用: {task.ai_id} (原因: {task.reason})",
                metadata={"priority": task.priority, "reason": task.reason}
            )
            return task.ai_id
            
        # 没有优先级任务时，从符合条件的AI中随机选择
        eligible = self._get_eligible_speakers()
        if not eligible:
            self.logger.warning("没有符合条件的AI可以发言")
            return None
            
        speaker_id = random.choice(eligible)
        self.last_speaker = speaker_id
        
        # 如果是第一个发言的AI，记录下来
        if not self.first_ai_id:
            self.first_ai_id = speaker_id
            
        return speaker_id
    
    def _get_eligible_speakers(self) -> List[str]:
        """获取有发言权限的AI列表"""
        eligible_set = set()
        excluded_ais = self.config_manager.system_config.excluded_ais
        
        for ai_id, ai_config in self.config_manager.ai_configs.items():
            # 排除配置中指定的AI
            if ai_id in excluded_ais:
                continue
                
            # 检查该AI是否有发送权限
            for channel_perms in ai_config.channels.values():
                if "send" in channel_perms:
                    eligible_set.add(ai_id)
                    break
        
        # 排除上一个发言的AI，增加多样性
        eligible = [ai for ai in eligible_set if ai != self.last_speaker]
        
        # 如果排除上一个发言者后没有可选的AI，则重置选择池
        if not eligible:
            eligible = list(eligible_set)
        
        return eligible
    
    def add_priority_task(self, ai_id: str, reason: str, priority: str = "B") -> None:
        """添加优先级任务"""
        task = PriorityTask(priority=priority, ai_id=ai_id, reason=reason)
        self.priority_queue.append(task)
        
    def process_ai_turn(self, speaker_id: str) -> bool:
        """处理AI的发言回合"""
        try:
            # 处理开场白
            if not self.first_ai_spoken and self.config_manager.system_config.opening_speech:
                if speaker_id == self.first_ai_id:
                    self._add_opening_speech(speaker_id)
                self.first_ai_spoken = True
            
            # 生成消息
            ai_config = self.config_manager.get_ai_config(speaker_id)
            
            # 确保会话格式正确
            session = self.ai_memories[speaker_id]
            if not session:
                session = [{"role": "system", "content": ai_config.prompt}]
            
            # 使用工具调用功能运行会话
            try:
                updated_session, response = self.chat_core.run_chat_session(
                    session, 
                    ai_config.api_index
                )
                
                # 更新AI的记忆
                self.ai_memories[speaker_id] = updated_session
                
                # 检查是否有工具调用结果需要处理
                if self._has_tool_calls(updated_session):
                    self._process_tool_call_results(speaker_id, updated_session)
                    return True
                
            except APIConnectionError as e:
                # 如果工具调用失败，回退到不使用工具的模式
                self.logger.warning(f"工具调用失败，回退到普通模式: {str(e)}", ai_id=speaker_id)
                
                # 临时禁用工具调用
                original_tool_callbacks = self.chat_core.tool_callbacks
                self.chat_core.tool_callbacks = None
                
                try:
                    updated_session, response = self.chat_core.run_chat_session(
                        session, 
                        ai_config.api_index
                    )
                    
                    # 更新AI的记忆
                    self.ai_memories[speaker_id] = updated_session
                    
                finally:
                    # 恢复工具调用
                    self.chat_core.tool_callbacks = original_tool_callbacks
            
            # 监察机制
            if not self.message_processor.monitor_message(speaker_id, response, self.chat_core):
                return False  # 消息被驳回
            
            # 解析和分发消息
            parsed_message = self.message_processor.parse_message(response, speaker_id)
            self._distribute_message(speaker_id, parsed_message)
            
            return True
            
        except Exception as e:
            self.logger.error(f"处理AI回合时出错: {str(e)}", ai_id=speaker_id)
            import traceback
            self.logger.error(f"详细错误信息: {traceback.format_exc()}", ai_id=speaker_id)
            return False
    
    def _has_tool_calls(self, session: List[Dict[str, Any]]) -> bool:
        """检查会话中是否有工具调用"""
        for message in session:
            if message.get("role") == "assistant" and message.get("tool_calls"):
                return True
        return False
    
    def _process_tool_call_results(self, speaker_id: str, session: List[Dict[str, Any]]) -> None:
        """处理工具调用结果"""
        for message in session:
            if message.get("role") == "assistant" and message.get("tool_calls"):
                # 记录工具调用
                for tool_call in message.get("tool_calls", []):
                    function_name = tool_call.get("function", {}).get("name", "")
                    self.logger.log_command(speaker_id, f"工具调用: {function_name}", "执行")
                
                # 检查是否有工具响应
                next_message = session[session.index(message) + 1] if session.index(message) + 1 < len(session) else None
                if next_message and next_message.get("role") == "tool":
                    tool_response = next_message.get("content", "")
                    self.logger.info(f"工具执行结果: {tool_response}", ai_id=speaker_id)
    
    def _add_opening_speech(self, speaker_id: str) -> None:
        """添加开场白"""
        opening_speech = self.config_manager.system_config.opening_speech
        self.logger.info(f"向第一个AI {speaker_id} 添加开场白")
        self.ai_memories[speaker_id].append({
            "role": "user",
            "content": opening_speech
        })
    
    def _distribute_message(self, speaker_id: str, parsed_message: ParsedMessage) -> None:
        """分发消息到各个频道和AI"""
        # 处理系统消息
        for sys_msg in parsed_message.system_messages:
            self.logger.info(f"[系统消息] {sys_msg}", ai_id=speaker_id)
            for ai_id in self.config_manager.ai_configs:
                self._add_system_message(ai_id, f"来自 {speaker_id} 的系统消息: {sys_msg}")
        
        # 分发主要消息
        if parsed_message.content:
            # 记录AI消息
            self.logger.log_ai_message(
                speaker_id, 
                parsed_message.content, 
                parsed_message.channels
            )
            
            # 添加到接收者的记忆
            for ai_id, ai_config in self.config_manager.ai_configs.items():
                for channel in parsed_message.channels:
                    if channel in ai_config.channels and "receive" in ai_config.channels[channel]:
                        role = "assistant" if ai_id == speaker_id else "user"
                        self.ai_memories[ai_id].append({
                            "role": role,
                            "content": f"[{channel}] {parsed_message.content}"
                        })
    
    def _add_system_message(self, ai_id: str, message: str) -> None:
        """添加系统消息到AI的记忆"""
        if ai_id in self.ai_memories:
            self.ai_memories[ai_id].append({
                "role": "system",
                "content": message
            })
    
    def run_main_loop(self) -> None:
        """运行主循环"""
        self.logger.info("多AI交流系统已启动")
        
        try:
            while True:
                self.round_count += 1
                
                # 选择发言人
                speaker_id = self.get_next_speaker()
                if not speaker_id:
                    time.sleep(5)
                    continue
                
                # 处理AI回合
                self.process_ai_turn(speaker_id)
                
                # 提示词轮换
                if self.prompt_manager.should_rotate_prompts(self.round_count):
                    self.prompt_manager.rotate_prompts(
                        self.round_count, self.chat_core, self.ai_memories
                    )
                
                # 控制节奏
                time.sleep(1)
                
        except KeyboardInterrupt:
            self.logger.info("系统被用户中断")
        except Exception as e:
            self.logger.error(f"系统致命错误: {str(e)}")