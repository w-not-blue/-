from typing import List, Dict, Any, Tuple, Optional
from concurrent.futures import ProcessPoolExecutor, as_completed
from multiprocessing import cpu_count
import numpy as np
from src.models.server import Server
from src.models.task import Task
from src.models.energy_model import EnergyModel
from src.models.temperature_model import TemperatureModel
from src.algorithms.greedy_algorithm import GreedyScheduler
from src.algorithms.genetic_algorithm import GeneticAlgorithm
from src.algorithms.round_robin import RoundRobinScheduler
from src.utils.logger import setup_logger
from src.utils.config import Config

logger = setup_logger("distributed_simulator")
config = Config()

class DistributedSimulator:
    def __init__(self, num_servers: int = 100, parallel_workers: int = None):
        self.num_servers = num_servers
        self.parallel_workers = parallel_workers or min(8, cpu_count())
        self.servers: List[Server] = []
        self.tasks: List[Task] = []
        self.energy_model = EnergyModel()
        self.temp_model = TemperatureModel()
        
        self._initialize_servers()

    def _initialize_servers(self):
        server_types = config.server_types
        for i in range(self.num_servers):
            rand = np.random.random()
            cum_prob = 0.0
            server_type = server_types[0]
            
            for st in server_types:
                cum_prob += st['proportion']
                if rand < cum_prob:
                    server_type = st
                    break
            
            self.servers.append(Server(
                server_id=i,
                max_load=server_type['max_load'],
                max_temp=server_type['max_temp'],
                base_power=server_type['base_power'],
                max_power=server_type['max_power'],
                efficiency=server_type.get('efficiency', 0.85)
            ))

    def _generate_tasks_for_hour(self, hour: int) -> List[Task]:
        tasks = []
        task_types = config.task_generation.get('task_types', [])
        
        base_rate = config.task_generation.get('base_rate', 10)
        peak_rate = config.task_generation.get('peak_rate', 50)
        peak_hours = config.task_generation.get('peak_hours', [9, 18])
        
        if peak_hours[0] <= hour < peak_hours[1]:
            task_count = int(np.random.poisson(peak_rate))
        else:
            task_count = int(np.random.poisson(base_rate))
        
        for i in range(task_count):
            rand = np.random.random()
            cum_prob = 0.0
            task_type = task_types[0] if task_types else None
            
            for tt in task_types:
                cum_prob += tt['proportion']
                if rand < cum_prob:
                    task_type = tt
                    break
            
            if task_type:
                load = np.random.randint(task_type['load_range'][0], task_type['load_range'][1] + 1)
                duration = np.random.randint(task_type['duration_range'][0], task_type['duration_range'][1] + 1)
                priority = np.random.randint(1, 6)
            else:
                load_range = config.task_generation.get('load_range', [5, 30])
                load = np.random.randint(load_range[0], load_range[1] + 1)
                duration = np.random.randint(1, 6)
                priority = np.random.randint(1, 6)
            
            tasks.append(Task(
                task_id=f"task_{hour}_{i}",
                load=load,
                priority=priority,
                deadline=hour + duration,
                created_time=hour
            ))
        
        return tasks

    def _simulate_single_hour(self, hour: int, server_snapshot: List[Dict[str, float]], 
                              pending_tasks: List[Task], algorithm: str) -> Dict[str, Any]:
        new_tasks = self._generate_tasks_for_hour(hour)
        all_tasks = pending_tasks + new_tasks
        
        servers = [Server(
            server_id=i,
            max_load=server_snapshot[i]['max_load'],
            max_temp=server_snapshot[i]['max_temp'],
            base_power=server_snapshot[i]['base_power'],
            max_power=server_snapshot[i]['max_power'],
            current_temp=server_snapshot[i]['current_temp'],
            current_load=server_snapshot[i]['current_load']
        ) for i in range(len(server_snapshot))]
        
        if algorithm == 'greedy':
            scheduler = GreedyScheduler(servers)
            assignments = scheduler.schedule_tasks(all_tasks)
        elif algorithm == 'genetic':
            scheduler = GeneticAlgorithm(servers, all_tasks)
            assignments = scheduler.schedule()
        elif algorithm == 'round_robin':
            scheduler = RoundRobinScheduler(servers)
            assignments = scheduler.schedule_tasks(all_tasks)
        else:
            assignments = []
        
        for task, server in assignments:
            server.assign_task(task)
        
        for server in servers:
            server.update_temperature()
        
        completed_tasks = [t for t in all_tasks if t.completed_time == hour]
        remaining_tasks = [t for t in all_tasks if t.completed_time is None]
        
        total_load = sum(s.current_load for s in servers)
        avg_temp = sum(s.current_temp for s in servers) / len(servers)
        total_energy = sum(self.energy_model.calculate_power(s) for s in servers)
        pue = self.energy_model.calculate_pue(total_energy, avg_temp)
        
        return {
            'hour': hour,
            'algorithm': algorithm,
            'total_load': total_load,
            'avg_temperature': avg_temp,
            'total_energy': total_energy,
            'pue': pue,
            'completed_tasks': len(completed_tasks),
            'remaining_tasks': len(remaining_tasks),
            'server_snapshot': [{
                'max_load': s.max_load,
                'max_temp': s.max_temp,
                'base_power': s.base_power,
                'max_power': s.max_power,
                'current_temp': s.current_temp,
                'current_load': s.current_load
            } for s in servers],
            'remaining_tasks_data': [{
                'task_id': t.task_id,
                'load': t.load,
                'priority': t.priority,
                'deadline': t.deadline,
                'created_time': t.created_time
            } for t in remaining_tasks]
        }

    def run_distributed_simulation(self, hours: int = 24, algorithm: str = 'greedy') -> List[Dict[str, Any]]:
        results = []
        server_snapshot = [{
            'max_load': s.max_load,
            'max_temp': s.max_temp,
            'base_power': s.base_power,
            'max_power': s.max_power,
            'current_temp': 25.0,
            'current_load': 0.0
        } for s in self.servers]
        pending_tasks: List[Task] = []
        
        logger.info(f"Starting distributed simulation with {self.num_servers} servers, {hours} hours, algorithm: {algorithm}")
        
        for hour in range(hours):
            result = self._simulate_single_hour(hour, server_snapshot, pending_tasks, algorithm)
            results.append(result)
            
            server_snapshot = result['server_snapshot']
            pending_tasks = [Task(**t) for t in result['remaining_tasks_data']]
            
            if hour % 6 == 0:
                logger.info(f"Completed hour {hour}/{hours}")
        
        logger.info(f"Distributed simulation completed. Total records: {len(results)}")
        return results

    def run_parallel_comparison(self, algorithms: List[str], hours: int = 24) -> Dict[str, List[Dict[str, Any]]]:
        results = {}
        
        def run_algorithm(algorithm):
            simulator = DistributedSimulator(num_servers=self.num_servers)
            return algorithm, simulator.run_distributed_simulation(hours=hours, algorithm=algorithm)
        
        with ProcessPoolExecutor(max_workers=min(len(algorithms), self.parallel_workers)) as executor:
            futures = {executor.submit(run_algorithm, algo): algo for algo in algorithms}
            
            for future in as_completed(futures):
                algo, algo_results = future.result()
                results[algo] = algo_results
                logger.info(f"Completed {algo} algorithm")
        
        return results

    def run_large_scale_test(self, server_counts: List[int], hours: int = 24) -> Dict[str, Any]:
        results = {}
        
        for count in server_counts:
            logger.info(f"Testing with {count} servers")
            simulator = DistributedSimulator(num_servers=count)
            result = simulator.run_distributed_simulation(hours=hours, algorithm='greedy')
            
            avg_pue = sum(r['pue'] for r in result) / len(result)
            avg_energy = sum(r['total_energy'] for r in result) / len(result)
            avg_temp = sum(r['avg_temperature'] for r in result) / len(result)
            
            results[count] = {
                'avg_pue': avg_pue,
                'avg_energy': avg_energy,
                'avg_temperature': avg_temp,
                'total_hours': hours,
                'server_count': count
            }
        
        return results