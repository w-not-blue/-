from typing import Dict, Any, Optional, List
from src.adapters.base import BaseAdapter, AdapterType, AdapterCapability
from src.adapters.local import LocalAdapter
from src.adapters.remote import RemoteAdapter
from src.adapters.cuda import CudaAdapter
from src.adapters.ascend import AscendAdapter
from src.utils.logger import setup_logger

logger = setup_logger("auto_adapter")

class AutoAdapter(BaseAdapter):
    """
    自动适配器 - 自动检测并选择最优适配策略
    
    检测优先级:
    1. 昇腾NPU (国产硬件优先)
    2. NVIDIA CUDA GPU
    3. 远程服务
    4. 本地CPU (兜底)
    
    参考项目: PyTorch Lightning auto-accelerator, ONNX Runtime
    """

    def __init__(self):
        self._available = True
        self._initialized = False
        self._adapters: List[BaseAdapter] = []
        self._active_adapter: Optional[BaseAdapter] = None
        self._adapter_map: Dict[str, BaseAdapter] = {}
        self._detect_order = []
        if AscendAdapter is not None:
            self._detect_order.append(('ascend', AscendAdapter))
        if CudaAdapter is not None:
            self._detect_order.append(('cuda', CudaAdapter))
        self._detect_order.extend([
            ('remote', RemoteAdapter),
            ('local', LocalAdapter),
        ])

    def get_type(self) -> AdapterType:
        return AdapterType.AUTO

    def get_capability(self) -> AdapterCapability:
        if self._active_adapter:
            return self._active_adapter.get_capability()
        return AdapterCapability(
            adapter_type=AdapterType.AUTO,
            max_servers=1000,
            supports_parallel=True,
            supports_gpu=True,
            supports_npu=True,
            supports_distributed=True,
            priority=0,
            description="自动适配器 (智能检测)"
        )

    def is_available(self) -> bool:
        return self._available

    def _detect_adapters(self) -> BaseAdapter:
        for name, adapter_cls in self._detect_order:
            adapter = adapter_cls()
            if adapter.is_available():
                self._adapters.append(adapter)
                self._adapter_map[name] = adapter
                logger.info(f"Adapter detected: {name} -> {adapter.get_capability().description}")
        
        for name, adapter_cls in self._detect_order:
            adapter = self._adapter_map.get(name)
            if adapter and adapter.is_available():
                logger.info(f"Selected adapter: {name}")
                return adapter
        
        local = LocalAdapter()
        local.is_available()
        logger.info("Fallback to LocalAdapter")
        return local

    def initialize(self, config: Optional[Dict[str, Any]] = None) -> bool:
        try:
            self._config = config or {}
            force = self._config.get('force_adapter')
            
            if force and force in self._adapter_map:
                self._active_adapter = self._adapter_map[force]
            elif force:
                adapter_cls_map = {
                    'local': LocalAdapter, 'remote': RemoteAdapter,
                }
                if CudaAdapter is not None:
                    adapter_cls_map['cuda'] = CudaAdapter
                if AscendAdapter is not None:
                    adapter_cls_map['ascend'] = AscendAdapter
                cls = adapter_cls_map.get(force)
                if cls:
                    self._active_adapter = cls()
            else:
                self._active_adapter = self._detect_adapters()
            
            if self._active_adapter:
                ok = self._active_adapter.initialize(self._config)
                if ok:
                    self._initialized = True
                    logger.info(f"AutoAdapter initialized with {self._active_adapter.get_type().value}")
                    return True
                logger.error("Active adapter init failed")
                fallback = LocalAdapter()
                if fallback.initialize(self._config):
                    self._active_adapter = fallback
                    self._initialized = True
                    return True
            
            return False
        except Exception as e:
            logger.error(f"AutoAdapter init failed: {e}")
            return False

    def execute(self, **kwargs) -> Dict[str, Any]:
        if not self._active_adapter:
            return {'success': False, 'error': 'No active adapter'}
        try:
            result = self._active_adapter.execute(**kwargs)
            result['adapter_type'] = self._active_adapter.get_type().value
            return result
        except Exception as e:
            logger.error(f"Adapter execution failed: {e}")
            fallback = LocalAdapter()
            fallback.initialize()
            return fallback.execute(**kwargs)

    def get_status(self) -> Dict[str, Any]:
        active_info = self._active_adapter.get_status() if self._active_adapter else {}
        available_adapters = []
        for name, adapter in self._adapter_map.items():
            available_adapters.append({
                'name': name,
                'type': adapter.get_type().value,
                'available': adapter.is_available(),
                'capability': adapter.get_capability().__dict__
            })
        
        return {
            'type': 'auto',
            'available': self._available,
            'initialized': self._initialized,
            'active_adapter': self._active_adapter.get_type().value if self._active_adapter else None,
            'active_info': active_info,
            'available_adapters': available_adapters
        }

    def get_active_adapter(self) -> Optional[BaseAdapter]:
        return self._active_adapter

    def cleanup(self) -> None:
        for adapter in self._adapters:
            adapter.cleanup()
        if self._active_adapter:
            self._active_adapter.cleanup()
        self._initialized = False
        logger.info("AutoAdapter cleaned up")
