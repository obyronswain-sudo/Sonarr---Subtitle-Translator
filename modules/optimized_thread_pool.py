"""
Optimized Thread Pool for Translation Processing
Replaces manual threading with efficient ThreadPoolExecutor
"""
import threading
import time
import psutil
from concurrent.futures import ThreadPoolExecutor, as_completed, Future
from typing import Callable, List, Any, Optional, Dict
from dataclasses import dataclass
from enum import Enum
import logging

class TaskPriority(Enum):
    HIGH = 1
    MEDIUM = 2
    LOW = 3

@dataclass
class Task:
    """Represents a task to be executed"""
    func: Callable
    args: tuple
    kwargs: dict
    priority: TaskPriority
    task_id: str
    callback: Optional[Callable] = None

class OptimizedThreadPool:
    """
    Advanced thread pool with resource-aware sizing and priority queuing
    """
    
    def __init__(self, logger=None):
        self.logger = logger
        self.executor = None
        self.futures: Dict[str, Future] = {}
        self.task_queue = []
        self.queue_lock = threading.Lock()
        self._stop_event = threading.Event()
        
        # Resource monitoring
        self.cpu_count = psutil.cpu_count(logical=False)
        self.memory_gb = psutil.virtual_memory().total / (1024**3)
        
        # Calculate optimal thread count
        self.max_workers = self._calculate_optimal_workers()
        
        # Statistics
        self.stats = {
            'tasks_submitted': 0,
            'tasks_completed': 0,
            'tasks_failed': 0,
            'total_execution_time': 0,
            'avg_execution_time': 0
        }
        
        self._start_executor()
        
        if self.logger:
            self.logger.log('info', f'✅ Thread pool iniciado com {self.max_workers} workers (CPU: {self.cpu_count}, RAM: {self.memory_gb:.1f}GB)')
    
    def _calculate_optimal_workers(self) -> int:
        """Calculate optimal number of workers based on system resources"""
        # Base calculation on CPU cores
        base_workers = max(2, self.cpu_count)
        
        # Adjust based on available memory
        if self.memory_gb < 4:
            return min(2, base_workers)
        elif self.memory_gb < 8:
            return min(4, base_workers)
        elif self.memory_gb < 16:
            return min(6, base_workers * 2)
        else:
            return min(12, base_workers * 2)
    
    def _start_executor(self):
        """Start the thread pool executor"""
        self.executor = ThreadPoolExecutor(
            max_workers=self.max_workers,
            thread_name_prefix='TranslationWorker'
        )
    
    def submit_task(self, func: Callable, *args, priority: TaskPriority = TaskPriority.MEDIUM, 
                   task_id: str = None, callback: Callable = None, **kwargs) -> str:
        """
        Submit a task to the thread pool
        
        Returns:
            task_id: Unique identifier for the task
        """
        if not task_id:
            task_id = f"task_{int(time.time() * 1000)}_{threading.current_thread().ident}"
        
        task = Task(
            func=func,
            args=args,
            kwargs=kwargs,
            priority=priority,
            task_id=task_id,
            callback=callback
        )
        
        with self.queue_lock:
            self.task_queue.append(task)
            # Sort by priority (lower number = higher priority)
            self.task_queue.sort(key=lambda t: t.priority.value)
        
        # Submit to executor
        future = self.executor.submit(self._execute_task, task)
        self.futures[task_id] = future
        self.stats['tasks_submitted'] += 1
        
        return task_id
    
    def _execute_task(self, task: Task) -> Any:
        """Execute a single task with error handling"""
        start_time = time.time()
        task_id = task.task_id
        
        try:
            if self.logger:
                self.logger.log('debug', f'🔄 Executando task {task_id}')
            
            # Execute the task
            result = task.func(*task.args, **task.kwargs)
            
            # Execute callback if provided
            if task.callback:
                task.callback(result)
            
            execution_time = time.time() - start_time
            self.stats['tasks_completed'] += 1
            self.stats['total_execution_time'] += execution_time
            self.stats['avg_execution_time'] = self.stats['total_execution_time'] / self.stats['tasks_completed']
            
            if self.logger:
                self.logger.log('debug', f'✅ Task {task_id} concluída em {execution_time:.2f}s')
            
            return result
            
        except Exception as e:
            self.stats['tasks_failed'] += 1
            if self.logger:
                self.logger.log('error', f'❌ Task {task_id} falhou: {e}')
            raise
    
    def submit_batch(self, tasks: List[tuple]) -> List[str]:
        """
        Submit multiple tasks efficiently
        
        Args:
            tasks: List of (func, args, kwargs, priority, callback) tuples
        
        Returns:
            List of task IDs
        """
        task_ids = []
        
        for task_data in tasks:
            if len(task_data) >= 2:
                func, args = task_data[0], task_data[1]
                kwargs = task_data[2] if len(task_data) > 2 else {}
                priority = task_data[3] if len(task_data) > 3 else TaskPriority.MEDIUM
                callback = task_data[4] if len(task_data) > 4 else None
                
                task_id = self.submit_task(func, *args, priority=priority, callback=callback, **kwargs)
                task_ids.append(task_id)
        
        return task_ids
    
    def wait_completion(self, task_ids: List[str] = None, timeout: Optional[float] = None) -> Dict[str, Any]:
        """
        Wait for completion of specified tasks or all tasks
        
        Returns:
            Dict with task_id -> result/error
        """
        if task_ids is None:
            task_ids = list(self.futures.keys())
        
        results = {}
        futures_to_wait = [self.futures[tid] for tid in task_ids if tid in self.futures]
        
        try:
            for future in as_completed(futures_to_wait, timeout=timeout):
                # Find the task ID for this future
                task_id = None
                for tid, f in self.futures.items():
                    if f == future:
                        task_id = tid
                        break
                
                if task_id:
                    try:
                        results[task_id] = future.result()
                    except Exception as e:
                        results[task_id] = f"Error: {e}"
                        
        except TimeoutError:
            if self.logger:
                self.logger.log('warning', f'Timeout esperando conclusão das tasks')
        
        return results
    
    def get_stats(self) -> Dict[str, Any]:
        """Get thread pool statistics"""
        return {
            **self.stats,
            'active_tasks': len([f for f in self.futures.values() if f.running()]),
            'pending_tasks': len(self.task_queue),
            'max_workers': self.max_workers,
            'utilization': f"{(self.stats['tasks_completed'] / max(1, self.stats['tasks_submitted'])) * 100:.1f}%"
        }
    
    def shutdown(self, wait: bool = True):
        """Shutdown the thread pool gracefully"""
        if self.executor:
            self.executor.shutdown(wait=wait)
            if self.logger:
                self.logger.log('info', '🛑 Thread pool encerrado')
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.shutdown(wait=True)

class ResourceAwareBatchProcessor:
    """
    Process batches of tasks with resource awareness
    """
    
    def __init__(self, thread_pool: OptimizedThreadPool, logger=None):
        self.thread_pool = thread_pool
        self.logger = logger
        self.memory_threshold = 0.8  # 80% memory usage threshold
    
    def process_batch_adaptive(self, tasks: List[tuple], max_batch_size: int = None) -> Dict[str, Any]:
        """
        Process tasks in adaptive batches based on system resources
        """
        if not max_batch_size:
            # Calculate adaptive batch size
            max_batch_size = self._calculate_adaptive_batch_size(len(tasks))
        
        if self.logger:
            self.logger.log('info', f'📦 Processando {len(tasks)} tasks em batches de {max_batch_size}')
        
        results = {}
        total_batches = (len(tasks) + max_batch_size - 1) // max_batch_size
        
        for i in range(0, len(tasks), max_batch_size):
            batch = tasks[i:i + max_batch_size]
            batch_id = f"batch_{i//max_batch_size + 1}_{total_batches}"
            
            if self.logger:
                self.logger.log('info', f'🔄 Processando {batch_id} ({len(batch)} tasks)')
            
            # Submit batch
            batch_task_ids = self.thread_pool.submit_batch(batch)
            
            # Wait for batch completion
            batch_results = self.thread_pool.wait_completion(batch_task_ids)
            results.update(batch_results)
            
            # Check memory usage and throttle if needed
            memory_usage = psutil.virtual_memory().percent / 100
            if memory_usage > self.memory_threshold:
                if self.logger:
                    self.logger.log('warning', f'⚠️ Uso de memória alto ({memory_usage:.1%}), pausando...')
                time.sleep(1.0)  # Throttle
        
        return results
    
    def _calculate_adaptive_batch_size(self, total_tasks: int) -> int:
        """Calculate optimal batch size based on available resources"""
        # Base batch size on memory and CPU
        memory_factor = int(self.memory_gb * 2)  # More memory = larger batches
        cpu_factor = self.cpu_count
        
        # Calculate based on task complexity (simple heuristic)
        estimated_memory_per_task = 10 * 1024 * 1024  # 10MB per task estimate
        available_memory = psutil.virtual_memory().available
        
        max_memory_based = max(1, available_memory // estimated_memory_per_task)
        max_cpu_based = cpu_factor * 4  # 4 tasks per CPU core
        
        # Choose the most restrictive
        adaptive_size = min(max_memory_based, max_cpu_based, total_tasks)
        
        # Ensure reasonable bounds
        return max(1, min(adaptive_size, 50))  # Cap at 50 tasks per batch

# Global thread pool instance for reuse
_global_thread_pool = None
_global_batch_processor = None
_thread_pool_lock = threading.Lock()

def get_global_thread_pool(logger=None) -> OptimizedThreadPool:
    """Get or create global thread pool instance"""
    global _global_thread_pool
    
    if _global_thread_pool is None:
        with _thread_pool_lock:
            if _global_thread_pool is None:
                _global_thread_pool = OptimizedThreadPool(logger)
    
    return _global_thread_pool

def get_global_batch_processor(logger=None) -> ResourceAwareBatchProcessor:
    """Get or create global batch processor instance"""
    global _global_batch_processor
    
    if _global_batch_processor is None:
        with _thread_pool_lock:
            if _global_batch_processor is None:
                thread_pool = get_global_thread_pool(logger)
                _global_batch_processor = ResourceAwareBatchProcessor(thread_pool, logger)
    
    return _global_batch_processor

def cleanup_global_pool():
    """Cleanup global thread pool resources"""
    global _global_thread_pool, _global_batch_processor
    
    with _thread_pool_lock:
        if _global_thread_pool:
            _global_thread_pool.shutdown()
            _global_thread_pool = None
        _global_batch_processor = None