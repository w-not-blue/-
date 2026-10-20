"""
gRPC/WebSocket 适配层

提供跨语言接口支持:
- gRPC: 高性能RPC（Go/Java/C#/Python客户端）
- WebSocket: 实时数据推送（Web浏览器、实时监控）

参考项目: gRPC (Google), Socket.IO, Flask-SocketIO
"""

import json
import time
from typing import Dict, Any, Generator, Optional
from src.utils.logger import setup_logger
from src.utils.config import Config

logger = setup_logger("grpc_adapter")
config = Config()

class GrpcServerAdapter:
    """
    gRPC服务端适配器
    
    使系统可通过gRPC协议被跨语言客户端调用。
    Proto定义: proto/scheduler.proto
    
    参考项目: gRPC, grpcio
    """

    def __init__(self, host: str = '0.0.0.0', port: int = 50051):
        self.host = host
        self.port = port
        self._server = None

    def start(self) -> bool:
        try:
            import grpc
            from concurrent import futures
            import proto.scheduler_pb2 as pb2
            import proto.scheduler_pb2_grpc as pb2_grpc
            
            self._server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
            pb2_grpc.add_DataCenterSchedulerServicer_to_server(
                _GrpcServiceImpl(), self._server
            )
            self._server.add_insecure_port(f'{self.host}:{self.port}')
            self._server.start()
            logger.info(f"gRPC server started on {self.host}:{self.port}")
            return True
        except ImportError:
            logger.warning("grpcio not installed, gRPC server unavailable")
            return False
        except Exception as e:
            logger.error(f"gRPC server start failed: {e}")
            return False

    def stop(self) -> None:
        if self._server:
            self._server.stop(0)
            logger.info("gRPC server stopped")

    def wait_for_termination(self) -> None:
        if self._server:
            self._server.wait_for_termination()


class _GrpcServiceImpl:
    """gRPC服务实现（自动生成client后替换为实际实现）"""

    def RunSimulation(self, request, context):
        from src.plugin import PluginManager
        from src.plugin.simulation_plugin import SimulationPlugin
        
        pm = PluginManager()
        pm.register_plugin(SimulationPlugin)
        
        plugin_config = {
            'num_servers': request.num_servers,
            'simulation_hours': request.simulation_hours,
            'parallel_workers': request.parallel_workers,
            'run_mode': request.run_mode,
            'server_type': request.server_type
        }
        
        pm.load_plugin('SimulationPlugin', plugin_config)
        result = pm.execute_plugin('SimulationPlugin',
            algorithms=list(request.algorithms),
            visualization=request.visualization,
            save_results=request.save_results
        )
        pm.unload_plugin('SimulationPlugin')
        
        return result

    def RunSimulationStream(self, request, context):
        total_hours = request.simulation_hours
        for hour in range(total_hours):
            result = {
                'time': hour,
                'algorithm': request.algorithms[0] if request.algorithms else 'greedy',
                'total_load': 100.0,
                'avg_temperature': 25.0 + 10.0 * (hour % 12) / 12,
                'total_energy': 500.0,
                'pue': 1.75
            }
            yield result
            time.sleep(0.1)

    def GetMetrics(self, request, context):
        from src.utils import load_simulation_data
        df = load_simulation_data()
        if df is None:
            return {'success': False, 'data': {}}
        return {'success': True, 'data': df.to_dict('records')}

    def HealthCheck(self, request, context):
        return {
            'status': 'healthy',
            'service': 'datacenter-scheduler',
            'timestamp': str(time.time())
        }

    def ListAlgorithms(self, request, context):
        return {
            'algorithms': [
                {'name': 'greedy', 'description': '贪心调度算法', 'type': '实时调度'},
                {'name': 'genetic', 'description': '遗传优化算法', 'type': '全局优化'},
                {'name': 'round_robin', 'description': '轮询调度算法', 'type': '基准算法'}
            ]
        }


class WebSocketAdapter:
    """
    WebSocket适配器
    
    提供实时数据推送能力，适用于:
    - Web可视化面板实时更新
    - 移动端App数据推送
    - 第三方监控系统集成
    
    参考项目: Socket.IO, Flask-SocketIO, WebSockets (RFC 6455)
    """

    def __init__(self):
        self._clients = set()
        self._running = False

    def register(self, ws_server) -> None:
        """注册到WebSocket服务器"""
        self._running = True
        logger.info("WebSocket adapter registered")

    async def broadcast_metric_update(self, data: Dict[str, Any]) -> None:
        """广播指标更新到所有客户端"""
        pass

    async def broadcast_simulation_progress(self, progress: float, message: str) -> None:
        """广播仿真进度"""
        pass

    async def handle_client_message(self, client_id: str, message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """处理客户端消息"""
        action = message.get('action', '')
        
        handlers = {
            'run_simulation': self._handle_run_simulation,
            'get_metrics': self._handle_get_metrics,
            'ping': self._handle_ping
        }
        
        handler = handlers.get(action)
        if handler:
            return await handler(message)
        return {'error': f'Unknown action: {action}'}

    async def _handle_run_simulation(self, message: Dict[str, Any]) -> Dict[str, Any]:
        from src.sdk import DataCenterSimulatorSDK
        sdk = DataCenterSimulatorSDK(mode='local')
        result = sdk.run_simulation(**message.get('params', {}))
        return result

    async def _handle_get_metrics(self, message: Dict[str, Any]) -> Dict[str, Any]:
        from src.sdk import DataCenterSimulatorSDK
        sdk = DataCenterSimulatorSDK(mode='local')
        result = sdk.get_metrics()
        return result

    async def _handle_ping(self, message: Dict[str, Any]) -> Dict[str, Any]:
        return {'action': 'pong', 'timestamp': time.time()}
