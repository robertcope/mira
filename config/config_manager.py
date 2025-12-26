"""
Main configuration module for the application.

Provides centralized configuration management with validation, loading from
multiple sources, and a clean access interface.
"""

import logging
from pathlib import Path
from typing import Any, Dict, List

from pydantic import BaseModel, Field

from config.config import (
    ApiConfig,
    ApiServerConfig,
    PathConfig,
    ToolConfig,
    SystemConfig,
    EmbeddingsConfig,
    LTMemoryConfig,
    DomainKnowledgeConfig,
    LatticeConfig,
    ContextConfig,
)

# Import the registry from tools package
from tools.registry import registry


class AppConfig(BaseModel):
    """Configuration manager with Vault integration and dynamic tool configuration via registry."""
    
    api: ApiConfig = Field(default_factory=ApiConfig)
    api_server: ApiServerConfig = Field(default_factory=ApiServerConfig)
    paths: PathConfig = Field(default_factory=PathConfig)
    tools: ToolConfig = Field(default_factory=ToolConfig)
    system: SystemConfig = Field(default_factory=SystemConfig)
    embeddings: EmbeddingsConfig = Field(default_factory=EmbeddingsConfig)
    lt_memory: LTMemoryConfig = Field(default_factory=LTMemoryConfig)
    domain_knowledge: DomainKnowledgeConfig = Field(default_factory=DomainKnowledgeConfig)
    lattice: LatticeConfig = Field(default_factory=LatticeConfig)
    context: ContextConfig = Field(default_factory=ContextConfig)
    
    # System prompt loaded once at startup
    system_prompt_text: str = Field(default="", exclude=True)
    
    # Cache for tool configs (non-model field, excluded from serialization)
    tool_configs: Dict[str, BaseModel] = Field(default_factory=dict, exclude=True)

    @classmethod
    def load(cls) -> "AppConfig":
        """Load configuration with defaults and system prompt."""
        logger = logging.getLogger(__name__)
        
        try:
            instance = cls()
            instance._load_system_prompt()
            logger.info("Configuration initialized successfully")
            return instance
        except Exception as e:
            logger.error(f"Configuration initialization failed: {e}")
            raise ValueError(f"Error initializing configuration: {e}")
    
    
    
    def get(self, key: str, default: Any = None) -> Any:
        parts = key.split(".")
        
        if len(parts) == 1:
            # Top-level attribute
            return getattr(self, parts[0], default)
        
        if len(parts) == 2:
            # Nested attribute
            section = getattr(self, parts[0], None)
            if section is None:
                return default
            return getattr(section, parts[1], default)
        
        # Unsupported nesting level
        return default
    
    def require(self, key: str) -> Any:
        value = self.get(key)
        if value is None:
            raise KeyError(f"Required configuration key not found: {key}")
        return value
    
    @property
    def api_key(self) -> str:
        """Gets Anthropic API key from Vault."""
        from clients.vault_client import get_api_key
        return get_api_key(self.api.api_key_name)
        
        
    @property
    def google_maps_api_key(self) -> str:
        from clients.vault_client import get_api_key
        return get_api_key('google_maps_api_key')
        
    
    @property
    def embeddings_api_key(self) -> str:
        if self.embeddings.provider == "local":
            return ""
            
        from clients.vault_client import get_api_key
        return get_api_key('openai_embeddings_key')
    
    @property
    def lt_memory_api_key(self) -> str:
        if self.lt_memory.llm_provider == "local":
            return ""
            
        from clients.vault_client import get_api_key
        return get_api_key(self.lt_memory.llm_api_key_name)
    
    def as_dict(self) -> Dict[str, Any]:
        return self.model_dump(exclude={"prompt_cache"})
    
    def _load_system_prompt(self) -> None:
        """Load system prompt once at startup."""
        file_path = Path("config/system_prompt.txt")
        
        if not file_path.exists():
            raise FileNotFoundError(f"System prompt not found: {file_path}")
        
        try:
            with open(file_path, 'r') as f:
                self._system_prompt = f.read().strip()
            logging.info("System prompt loaded")
        except Exception as e:
            raise RuntimeError(f"Error loading system prompt: {e}")
    
    @property
    def system_prompt(self) -> str:
        """Get the system prompt."""
        return self._system_prompt
    
        
    def __getattr__(self, name: str) -> Any:
        """Dynamic tool configuration access via registry - enables config.tool_name syntax."""
        # Check if this might be a tool configuration
        if name.endswith('_tool') or name in self.tool_configs:
            # Get the tool configuration
            return self.get_tool_config(name)
            
        # Not a tool config, raise normal attribute error
        raise AttributeError(f"'AppConfig' object has no attribute '{name}'")
    
    def get_tool_config(self, tool_name: str) -> BaseModel:
        """Gets/creates tool config via registry with caching."""
        if tool_name not in self.tool_configs:
            try:
                config_class = registry.get_or_create(tool_name)
                config_instance = config_class()
                self.tool_configs[tool_name] = config_instance
                
                logging.debug(f"Tool config created: {tool_name}")
                
            except Exception as e:
                logging.error(f"Tool config creation failed for {tool_name}: {e}")
                raise ValueError(f"Error creating tool configuration for '{tool_name}': {e}")
                
        return self.tool_configs[tool_name]
    
    # We don't need a discover_tools method anymore.
    # Tools register themselves when they're imported naturally by the application.
            
    def list_available_tool_configs(self) -> List[str]:
        cached_configs = list(self.tool_configs.keys())
        registry_configs = list(registry._registry.keys())
        return list(set(cached_configs + registry_configs))


# Initialize configuration
def initialize_config() -> AppConfig:
    """Initialize configuration and logging."""
    try:
        config_instance = AppConfig.load()
        
        logging.basicConfig(
            level=getattr(logging, config_instance.system.log_level),
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        )
        
        logging.info("Configuration loaded successfully")
        logging.info(f"Registry initialized with: {list(registry._registry.keys())}")
        
        return config_instance
        
    except Exception as e:
        error_msg = f"Error initializing configuration: {e}"
        try:
            logging.error(error_msg)
        except:
            pass
        
        raise RuntimeError(error_msg)

# Create the global configuration instance
config = initialize_config()
