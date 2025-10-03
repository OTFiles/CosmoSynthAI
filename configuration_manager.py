# configuration_manager.py
import json
from typing import Dict, Any, Optional, List
from pathlib import Path
from dataclasses import dataclass
from logging_system import UnifiedLogger

@dataclass
class AIConfig:
    """AI配置数据类"""
    ai_id: str
    prompt: str
    api_index: int
    channels: Dict[str, List[str]]  # channel -> permissions
    monitor: Optional[str] = None
    prompt_regeneration: Optional[Dict[str, Any]] = None

@dataclass
class SystemConfig:
    """系统配置数据类"""
    channel_manager_ai: Optional[str]
    memory_manager_ai: Optional[str]
    allowed_callers: List[str]
    excluded_ais: List[str]
    prompt_generators: List[Dict[str, Any]]
    opening_speech: str
    prompt_rotation_frequency: int
    observer_config: Optional[Dict[str, Any]] = None

class ConfigurationManager:
    """配置管理器，负责加载和验证系统配置"""
    
    def __init__(self, logger: UnifiedLogger):
        self.logger = logger
        self.ai_configs: Dict[str, AIConfig] = {}
        self.system_config: Optional[SystemConfig] = None
        self.api_configs: Dict[str, Any] = {}
    
    def load_api_config(self, config_path: str) -> None:
        """加载API配置"""
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config_data = json.load(f)
                # 适配新的ChatCore配置格式
                if "configs" in config_data:
                    self.api_configs = config_data["configs"]
                else:
                    self.api_configs = config_data
            self.logger.info(f"成功加载API配置，共 {len(self.api_configs)} 个API")
        except Exception as e:
            self.logger.error(f"加载API配置失败: {str(e)}")
            raise ConfigError(f"API配置加载失败: {str(e)}")
    
    def load_tool_config(self, config_path: str) -> None:
        """加载工具配置"""
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                tool_config = json.load(f)
            
            self._validate_tool_config(tool_config)
            self._parse_ai_configs(tool_config)
            self._parse_system_config(tool_config)
            
            self.logger.info(f"成功加载工具配置，共 {len(self.ai_configs)} 个AI")
            
        except Exception as e:
            self.logger.error(f"加载工具配置失败: {str(e)}")
            raise ConfigError(f"工具配置加载失败: {str(e)}")
    
    def _validate_tool_config(self, config: Dict[str, Any]) -> None:
        """验证工具配置完整性"""
        if "AI" not in config:
            raise ConfigError("配置文件中缺少AI定义")
        
        # 验证必需的AI配置字段
        for ai_id, ai_config in config["AI"].items():
            if "prompt" not in ai_config:
                raise ConfigError(f"AI '{ai_id}' 缺少prompt配置")
            if "api" not in ai_config:
                raise ConfigError(f"AI '{ai_id}' 缺少api配置")
    
    def _parse_ai_configs(self, tool_config: Dict[str, Any]) -> None:
        """解析AI配置"""
        self.ai_configs.clear()
        
        for ai_id, ai_config in tool_config["AI"].items():
            # 提取频道权限
            channels = {}
            for key, value in ai_config.items():
                if key not in ["prompt", "monitor", "api", "prompt_regeneration"]:
                    channels[key] = value
            
            # 创建AI配置对象
            self.ai_configs[ai_id] = AIConfig(
                ai_id=ai_id,
                prompt=ai_config.get("prompt", ""),
                api_index=ai_config["api"],
                channels=channels,
                monitor=ai_config.get("monitor"),
                prompt_regeneration=ai_config.get("prompt_regeneration")
            )
    
    def _parse_system_config(self, tool_config: Dict[str, Any]) -> None:
        """解析系统配置"""
        # 验证频道管理AI
        channel_manager_ai = tool_config.get("channel_manager_ai")
        if channel_manager_ai and channel_manager_ai not in self.ai_configs:
            self.logger.warning(f"频道管理AI '{channel_manager_ai}' 未在AI配置中定义")
            channel_manager_ai = None
        
        # 验证记忆管理AI
        memory_manager_ai = tool_config.get("memory_manager_ai")
        if memory_manager_ai and memory_manager_ai not in self.ai_configs:
            self.logger.warning(f"记忆管理AI '{memory_manager_ai}' 未在AI配置中定义")
            memory_manager_ai = None
        
        # 验证排除的AI列表
        excluded_ais = tool_config.get("excluded_ais", [])
        valid_excluded_ais = [ai for ai in excluded_ais if ai in self.ai_configs]
        if len(valid_excluded_ais) != len(excluded_ais):
            invalid_ais = set(excluded_ais) - set(valid_excluded_ais)
            self.logger.warning(f"排除的AI配置无效: {invalid_ais}")
        
        # 验证提示词生成器配置
        prompt_generators = tool_config.get("prompt_generators", [])
        valid_generators = []
        for gen in prompt_generators:
            if self._validate_prompt_generator(gen):
                valid_generators.append(gen)
        
        self.system_config = SystemConfig(
            channel_manager_ai=channel_manager_ai,
            memory_manager_ai=memory_manager_ai,
            allowed_callers=tool_config.get("allowed_callers", []),
            excluded_ais=valid_excluded_ais,
            prompt_generators=valid_generators,
            opening_speech=tool_config.get("opening_speech", ""),
            prompt_rotation_frequency=tool_config.get("prompt_rotation_frequency", 100),
            observer_config=tool_config.get("observer")
        )
    
    def _validate_prompt_generator(self, generator: Dict[str, Any]) -> bool:
        """验证提示词生成器配置"""
        if "id" not in generator or "AI" not in generator or "source_channel" not in generator:
            self.logger.warning(f"提示词生成AI配置无效: {generator}")
            return False
        
        if not isinstance(generator["id"], int) or generator["id"] < 0:
            self.logger.warning(f"提示词生成AI配置无效: id必须是正整数 ({generator})")
            return False
        
        if generator["AI"] not in self.ai_configs:
            self.logger.warning(f"提示词生成AI '{generator['AI']}' 未在AI配置中定义")
            return False
        
        return True
    
    def get_ai_config(self, ai_id: str) -> AIConfig:
        """获取AI配置"""
        if ai_id not in self.ai_configs:
            raise AINotFoundError(f"AI '{ai_id}' 未定义")
        return self.ai_configs[ai_id]
    
    def get_ai_with_send_permission(self, channel: str) -> List[str]:
        """获取在指定频道有发送权限的AI列表"""
        return [ai_id for ai_id, config in self.ai_configs.items() 
                if channel in config.channels and "send" in config.channels[channel]]
    
    def get_ai_with_receive_permission(self, channel: str) -> List[str]:
        """获取在指定频道有接收权限的AI列表"""
        return [ai_id for ai_id, config in self.ai_configs.items() 
                if channel in config.channels and "receive" in config.channels[channel]]

class ConfigError(Exception):
    """配置相关异常"""
    pass

class AINotFoundError(Exception):
    """AI未找到异常"""
    pass