from typing import List, Dict, Any, Tuple, Optional
import random
import numpy as np
from concurrent.futures import ProcessPoolExecutor, as_completed
from multiprocessing import cpu_count
from src.models.server import Server
from src.models.task import Task
from src.models.energy_model import EnergyModel
from src.models.temperature_model import TemperatureModel
from src.utils.logger import setup_logger
from src.utils.config import Config
from src.algorithms.base import BaseScheduler

logger = setup_logger("genetic_algorithm")
config = Config()

def _default_seed() -> Optional[int]:
    """Read default seed from config; return None when unavailable."""
    try:
        sim_cfg = Config().get("simulation", {}) or {}
        seed_val = sim_cfg.get("random_seed")
        if seed_val is None:
            return None
        return int(seed_val)
    except Exception:
        return None


class GeneticScheduler(BaseScheduler):
    def __init__(self, servers: List[Server], seed: Optional[int] = None):
        effective_seed = seed if seed is not None else _default_seed()
        super().__init__(servers)
        self.logger = setup_logger("genetic_scheduler")
        self.config = Config()
        self.population_size = self.config.genetic.get('population_size', 50)
        self.generations = self.config.genetic.get('generations', 50)
        self.crossover_rate = self.config.genetic.get('crossover_rate', 0.8)
        self.mutation_rate = self.config.genetic.get('mutation_rate', 0.1)
        self.elitism_rate = self.config.genetic.get('elitism_rate', 0.1)
        self.seed = effective_seed
        self.rng = random.Random(effective_seed)
        self.np_rng = np.random.default_rng(effective_seed)
        self._pool: Optional[ProcessPoolExecutor] = None

    def _get_pool(self) -> ProcessPoolExecutor:
        if self._pool is None:
            workers = self.config.genetic.get('parallel_evaluators', min(4, cpu_count()))
            self._pool = ProcessPoolExecutor(max_workers=workers)
        return self._pool

    def close(self) -> None:
        if self._pool is not None:
            self._pool.shutdown(wait=True)
            self._pool = None

    def repair_server(self, task: Task) -> Optional[Server]:
        """Least-loaded server that can still take the task.

        The genetic search produces a static assignment; when its chosen server
        turns out to be full the task used to be dropped, which punished the
        algorithm for a fixable artefact of the encoding rather than for a bad
        placement decision.
        """
        candidates = [
            server for server in self.servers
            if not server.is_overloaded()
            and not server.is_overheated()
            and server.current_load + task.load <= server.max_load
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda s: (s.current_load, s.current_temp))

    def schedule_tasks(self, tasks: List[Task]) -> List[Tuple[Task, Optional[Server]]]:
        if not tasks:
            return []

        ga = GeneticAlgorithm(self.servers, tasks, seed=self.seed)

        results = []
        try:
            assignments = ga.schedule()
            for task, server in assignments:
                placed: Optional[Server] = None
                if server and not server.is_overloaded() and not server.is_overheated():
                    if server.assign_task(task):
                        placed = server
                if placed is None:
                    repair = self.repair_server(task)
                    if repair is not None and repair.assign_task(task):
                        placed = repair
                results.append((task, placed))
        finally:
            self.close()

        return results

    def get_name(self) -> str:
        return "genetic"


class GeneticAlgorithm:
    def __init__(self, servers: List[Server], tasks: List[Task], seed: Optional[int] = None):
        self.servers = servers
        self.tasks = tasks
        self.num_servers = len(servers)
        self.num_tasks = len(tasks)
        effective_seed = seed if seed is not None else _default_seed()
        self.seed = effective_seed
        self.rng = random.Random(effective_seed)
        self.np_rng = np.random.default_rng(effective_seed)
        self.population_size = config.genetic.get('population_size', 50)
        self.max_population_size = config.genetic.get('max_population_size', 200)
        self.generations = config.genetic.get('generations', 100)
        self.max_generations = config.genetic.get('max_generations', 500)
        self.crossover_rate = config.genetic.get('crossover_rate', 0.8)
        self.mutation_rate = config.genetic.get('mutation_rate', 0.1)
        self.elitism_rate = config.genetic.get('elitism_rate', 0.1)
        self.overload_penalty = config.genetic.get('overload_penalty', 100)
        self.overheat_penalty = config.genetic.get('overheat_penalty', 200)
        self.energy_weight = config.genetic.get('energy_weight', 0.5)
        self.temperature_weight = config.genetic.get('temperature_weight', 0.3)
        self.load_weight = config.genetic.get('load_weight', 0.2)
        self.enable_parallel = config.genetic.get('enable_parallel', True)
        self.parallel_evaluators = config.genetic.get('parallel_evaluators', min(4, cpu_count()))

        self.energy_model = EnergyModel()
        self.temp_model = TemperatureModel()

        if self.num_tasks == 0:
            raise ValueError("No tasks to schedule")

    def initialize_population(self) -> List[List[int]]:
        population = []
        for _ in range(self.population_size):
            individual = [self.rng.randint(0, self.num_servers - 1) for _ in range(self.num_tasks)]
            population.append(individual)
        return population

    def calculate_fitness(self, individual: List[int]) -> float:
        server_loads = [0.0] * self.num_servers
        server_temps = [s.current_temp for s in self.servers]
        total_energy = 0.0

        for task_idx, server_idx in enumerate(individual):
            server_loads[server_idx] += self.tasks[task_idx].load

        for i, server in enumerate(self.servers):
            load_ratio = server_loads[i] / server.max_load
            temp_increase = load_ratio * 15
            server_temps[i] = min(server.max_temp, server_temps[i] + temp_increase)

            if load_ratio <= 0:
                total_energy += server.base_power * server.idle_power_factor
            else:
                base_power = server.base_power
                load_power = (server.max_power - server.base_power) * load_ratio
                total_energy += base_power + load_power

            temp_penalty = max(0, (server_temps[i] - 70) / 15)
            total_energy += temp_penalty * 10

        avg_load = sum(server_loads) / (self.num_servers * 100)
        avg_temp = sum(server_temps) / self.num_servers

        energy_score = 1 / (total_energy / 1000 + 1)
        load_score = 1 - avg_load
        temp_score = 1 - (avg_temp - 25) / 60

        fitness = (self.energy_weight * energy_score +
                   self.load_weight * load_score +
                   self.temperature_weight * temp_score)

        return max(0.0001, fitness)

    def evaluate_population_parallel(self, population: List[List[int]]) -> List[Tuple[float, List[int]]]:
        results = []
        workers = max(1, self.parallel_evaluators)
        with ProcessPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(self.calculate_fitness, ind): ind for ind in population}

            for future in as_completed(futures):
                individual = futures[future]
                try:
                    fitness = future.result()
                    results.append((fitness, individual))
                except Exception as e:
                    logger.error(f"Error evaluating individual: {e}")
                    results.append((0.0, individual))

        return sorted(results, key=lambda x: x[0], reverse=True)

    def evaluate_population_serial(self, population: List[List[int]]) -> List[Tuple[float, List[int]]]:
        results = []
        for individual in population:
            fitness = self.calculate_fitness(individual)
            results.append((fitness, individual))
        return sorted(results, key=lambda x: x[0], reverse=True)

    def evaluate_population(self, population: List[List[int]]) -> List[Tuple[float, List[int]]]:
        if self.enable_parallel and self.num_tasks > 100:
            return self.evaluate_population_parallel(population)
        else:
            return self.evaluate_population_serial(population)

    def select_parents(self, evaluated: List[Tuple[float, List[int]]]) -> Tuple[List[int], List[int]]:
        total_fitness = sum(f[0] for f in evaluated)
        if total_fitness == 0:
            return self.rng.sample([e[1] for e in evaluated], 2)

        selection_probs = [f[0] / total_fitness for f in evaluated]
        parent1 = self.rng.choices([e[1] for e in evaluated], weights=selection_probs)[0]
        parent2 = self.rng.choices([e[1] for e in evaluated], weights=selection_probs)[0]

        return parent1, parent2

    def crossover(self, parent1: List[int], parent2: List[int]) -> Tuple[List[int], List[int]]:
        if self.rng.random() > self.crossover_rate:
            return parent1.copy(), parent2.copy()

        crossover_point = self.rng.randint(1, self.num_tasks - 1)
        child1 = parent1[:crossover_point] + parent2[crossover_point:]
        child2 = parent2[:crossover_point] + parent1[crossover_point:]

        return child1, child2

    def mutate(self, individual: List[int]) -> List[int]:
        mutated = individual.copy()
        for i in range(len(mutated)):
            if self.rng.random() < self.mutation_rate:
                mutated[i] = self.rng.randint(0, self.num_servers - 1)
        return mutated

    def evolve(self) -> Tuple[List[int], float]:
        population = self.initialize_population()
        best_fitness = 0.0
        best_individual = None

        for gen in range(self.generations):
            evaluated = self.evaluate_population(population)

            current_best = evaluated[0]
            if current_best[0] > best_fitness:
                best_fitness = current_best[0]
                best_individual = current_best[1].copy()

            if gen % 10 == 0:
                avg_fitness = sum(f[0] for f in evaluated) / len(evaluated)
                logger.debug(f"Generation {gen}: Best fitness = {best_fitness:.4f}, Avg fitness = {avg_fitness:.4f}")

            elite_count = max(1, int(self.elitism_rate * self.population_size))
            new_population = [e[1].copy() for e in evaluated[:elite_count]]

            while len(new_population) < self.population_size:
                parent1, parent2 = self.select_parents(evaluated)
                child1, child2 = self.crossover(parent1, parent2)
                child1 = self.mutate(child1)
                child2 = self.mutate(child2)
                new_population.extend([child1, child2])

            new_population = new_population[:self.population_size]
            population = new_population

        logger.debug(f"Final best fitness: {best_fitness:.4f}")
        return best_individual, best_fitness

    def schedule(self) -> List[Tuple[Task, Server]]:
        if self.num_tasks == 0:
            return []

        logger.info(f"Starting genetic algorithm scheduling with {self.num_tasks} tasks on {self.num_servers} servers")

        try:
            best_assignment, fitness = self.evolve()

            assignments = []
            for task_idx, server_idx in enumerate(best_assignment):
                assignments.append((self.tasks[task_idx], self.servers[server_idx]))

            logger.info(f"Genetic algorithm completed. Fitness: {fitness:.4f}, Assignments: {len(assignments)}")
            return assignments

        except Exception as e:
            logger.error(f"Genetic algorithm failed: {e}", exc_info=True)
            return self.fallback_schedule()

    def fallback_schedule(self) -> List[Tuple[Task, Server]]:
        """Placement of last resort: temperature and capacity aware, never a blind overload."""
        assignments = []
        for task in self.tasks:
            candidates = [
                server for server in self.servers
                if not server.is_overloaded()
                and not server.is_overheated()
                and server.current_load + task.load <= server.max_load
            ]
            if not candidates:
                continue
            server = min(candidates, key=lambda s: (s.current_load, s.current_temp))
            server.assign_task(task)
            assignments.append((task, server))
        return assignments

    def run_large_scale_simulation(self, iterations: int = 10) -> Dict[str, Any]:
        results = []

        for i in range(iterations):
            logger.info(f"Running large-scale iteration {i+1}/{iterations}")
            _, fitness = self.evolve()
            results.append(fitness)

        return {
            'avg_fitness': sum(results) / len(results),
            'min_fitness': min(results),
            'max_fitness': max(results),
            'iterations': iterations
        }