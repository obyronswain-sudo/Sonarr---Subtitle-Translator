"""
Processing Pipeline for Translation
Unified pipeline that handles all processing steps efficiently
"""
import time
import logging
from typing import Any, Dict, List, Optional, Tuple, Callable
from dataclasses import dataclass
from enum import Enum
import threading
from pathlib import Path

class ProcessingStep(Enum):
    LANGUAGE_DETECTION = "language_detection"
    CONTENT_VALIDATION = "content_validation"
    TRANSLATION = "translation"
    QUALITY_VALIDATION = "quality_validation"
    POST_PROCESSING = "post_processing"
    CACHE_UPDATE = "cache_update"

@dataclass
class ProcessingContext:
    """Context object that carries data through the pipeline"""
    original_text: str
    source_lang: str = 'auto'
    target_lang: str = 'pt-BR'
    api_used: str = 'unknown'
    detected_lang: Optional[str] = None
    translated_text: Optional[str] = None
    validation_result: Optional[Dict[str, Any]] = None
    quality_score: Optional[float] = None
    processing_time: float = 0.0
    step_results: Dict[str, Any] = None
    metadata: Dict[str, Any] = None
    
    def __post_init__(self):
        if self.step_results is None:
            self.step_results = {}
        if self.metadata is None:
            self.metadata = {}

class ProcessingStepResult:
    """Result of a processing step"""
    def __init__(self, success: bool, data: Any = None, error: Optional[str] = None, 
                 processing_time: float = 0.0, metadata: Dict[str, Any] = None):
        self.success = success
        self.data = data
        self.error = error
        self.processing_time = processing_time
        self.metadata = metadata or {}

class ProcessingPipeline:
    """
    High-performance processing pipeline with:
    - Step-by-step processing
    - Error handling and recovery
    - Performance monitoring
    - Caching integration
    """
    
    def __init__(self, logger=None):
        self.logger = logger
        self.steps: List[Tuple[ProcessingStep, Callable]] = []
        self.pipeline_stats = {
            'total_executions': 0,
            'successful_executions': 0,
            'failed_executions': 0,
            'total_processing_time': 0.0,
            'avg_processing_time': 0.0,
            'step_stats': {}
        }
        self.lock = threading.RLock()
        
        # Initialize default pipeline
        self._setup_default_pipeline()
        
        if self.logger:
            self.logger.log('info', f'✅ Pipeline de processamento iniciado com {len(self.steps)} etapas')
    
    def _setup_default_pipeline(self):
        """Setup the default processing pipeline"""
        # Note: Individual step implementations will be injected
        # This is just the structure - actual implementations come from other modules
        pass
    
    def add_step(self, step: ProcessingStep, func: Callable, position: Optional[int] = None):
        """Add a processing step to the pipeline"""
        with self.lock:
            if position is None:
                self.steps.append((step, func))
            else:
                self.steps.insert(position, (step, func))
            
            # Initialize stats for this step
            self.pipeline_stats['step_stats'][step.value] = {
                'executions': 0,
                'successes': 0,
                'failures': 0,
                'total_time': 0.0,
                'avg_time': 0.0
            }
    
    def remove_step(self, step: ProcessingStep):
        """Remove a processing step from the pipeline"""
        with self.lock:
            self.steps = [(s, f) for s, f in self.steps if s != step]
    
    def clear_pipeline(self):
        """Clear all steps from the pipeline"""
        with self.lock:
            self.steps.clear()
            self.pipeline_stats['step_stats'].clear()
    
    def execute(self, context: ProcessingContext, skip_steps: Optional[List[ProcessingStep]] = None) -> ProcessingContext:
        """
        Execute the processing pipeline
        
        Args:
            context: Processing context with input data
            skip_steps: List of steps to skip (for optimization)
        
        Returns:
            Updated processing context
        """
        skip_steps = skip_steps or []
        start_time = time.time()
        
        with self.lock:
            self.pipeline_stats['total_executions'] += 1
        
        try:
            current_context = context
            
            for step, func in self.steps:
                # Skip if requested
                if step in skip_steps:
                    if self.logger:
                        self.logger.log('debug', f'⏭️ Pulando etapa: {step.value}')
                    continue
                
                # Execute step with timing
                step_start = time.time()
                
                try:
                    if self.logger:
                        self.logger.log('debug', f'🔄 Executando etapa: {step.value}')
                    
                    # Execute the step function
                    result = func(current_context)
                    
                    step_time = time.time() - step_start
                    
                    # Update step statistics
                    with self.lock:
                        step_stats = self.pipeline_stats['step_stats'].get(step.value, {})
                        step_stats['executions'] = step_stats.get('executions', 0) + 1
                        step_stats['successes'] = step_stats.get('successes', 0) + 1
                        step_stats['total_time'] = step_stats.get('total_time', 0) + step_time
                        step_stats['avg_time'] = step_stats['total_time'] / step_stats['executions']
                        self.pipeline_stats['step_stats'][step.value] = step_stats
                    
                    if isinstance(result, ProcessingContext):
                        current_context = result
                    else:
                        # If function returns data instead of context, update context
                        current_context.step_results[step.value] = result
                    
                    if self.logger:
                        self.logger.log('debug', f'✅ Etapa {step.value} concluída em {step_time:.3f}s')
                        
                except Exception as e:
                    step_time = time.time() - step_start
                    
                    # Update step failure statistics
                    with self.lock:
                        step_stats = self.pipeline_stats['step_stats'].get(step.value, {})
                        step_stats['executions'] = step_stats.get('executions', 0) + 1
                        step_stats['failures'] = step_stats.get('failures', 0) + 1
                        step_stats['total_time'] = step_stats.get('total_time', 0) + step_time
                        if step_stats['executions'] > 0:
                            step_stats['avg_time'] = step_stats['total_time'] / step_stats['executions']
                        self.pipeline_stats['step_stats'][step.value] = step_stats
                    
                    if self.logger:
                        self.logger.log('error', f'❌ Etapa {step.value} falhou: {e}')
                    
                    # Decide whether to continue or fail fast
                    if step in [ProcessingStep.LANGUAGE_DETECTION, ProcessingStep.CONTENT_VALIDATION]:
                        # Critical steps - fail fast
                        raise
                    else:
                        # Non-critical steps - continue with error
                        current_context.step_results[step.value] = ProcessingStepResult(
                            success=False, error=str(e), processing_time=step_time
                        )
            
            # Update overall statistics
            total_time = time.time() - start_time
            with self.lock:
                self.pipeline_stats['successful_executions'] += 1
                self.pipeline_stats['total_processing_time'] += total_time
                self.pipeline_stats['avg_processing_time'] = (
                    self.pipeline_stats['total_processing_time'] / self.pipeline_stats['successful_executions']
                )
            
            current_context.processing_time = total_time
            
            if self.logger:
                self.logger.log('info', f'✅ Pipeline concluído em {total_time:.3f}s')
            
            return current_context
            
        except Exception as e:
            # Update failure statistics
            total_time = time.time() - start_time
            with self.lock:
                self.pipeline_stats['failed_executions'] += 1
            
            if self.logger:
                self.logger.log('error', f'❌ Pipeline falhou após {total_time:.3f}s: {e}')
            
            # Return context with error information
            context.step_results['pipeline_error'] = str(e)
            context.processing_time = total_time
            return context
    
    def execute_batch(self, contexts: List[ProcessingContext], 
                     skip_steps: Optional[List[ProcessingStep]] = None) -> List[ProcessingContext]:
        """
        Execute pipeline on multiple contexts efficiently
        
        Args:
            contexts: List of processing contexts
            skip_steps: Steps to skip for optimization
        
        Returns:
            List of processed contexts
        """
        results = []
        
        for i, context in enumerate(contexts):
            if self.logger and i % 10 == 0:
                self.logger.log('info', f'📦 Processando lote: {i+1}/{len(contexts)}')
            
            result = self.execute(context, skip_steps)
            results.append(result)
        
        return results
    
    def get_stats(self) -> Dict[str, Any]:
        """Get comprehensive pipeline statistics"""
        with self.lock:
            total_executions = self.pipeline_stats['total_executions']
            success_rate = (self.pipeline_stats['successful_executions'] / max(1, total_executions)) * 100
            failure_rate = (self.pipeline_stats['failed_executions'] / max(1, total_executions)) * 100
            
            # Calculate step efficiency
            step_efficiency = {}
            for step_name, stats in self.pipeline_stats['step_stats'].items():
                executions = stats.get('executions', 0)
                successes = stats.get('successes', 0)
                efficiency = (successes / max(1, executions)) * 100 if executions > 0 else 0
                step_efficiency[step_name] = {
                    'efficiency': f"{efficiency:.1f}%",
                    'executions': executions,
                    'avg_time': f"{stats.get('avg_time', 0):.3f}s",
                    'total_time': f"{stats.get('total_time', 0):.3f}s"
                }
            
            return {
                'pipeline_overview': {
                    'total_executions': total_executions,
                    'successful_executions': self.pipeline_stats['successful_executions'],
                    'failed_executions': self.pipeline_stats['failed_executions'],
                    'success_rate': f"{success_rate:.1f}%",
                    'failure_rate': f"{failure_rate:.1f}%",
                    'avg_processing_time': f"{self.pipeline_stats['avg_processing_time']:.3f}s"
                },
                'step_efficiency': step_efficiency,
                'step_count': len(self.steps),
                'steps': [step.value for step, _ in self.steps]
            }
    
    def optimize_pipeline(self):
        """Optimize pipeline based on performance statistics"""
        stats = self.get_stats()
        
        # Identify slow steps
        slow_steps = []
        for step_name, efficiency in stats['step_efficiency'].items():
            avg_time = float(efficiency['avg_time'].rstrip('s'))
            if avg_time > 1.0:  # More than 1 second average
                slow_steps.append(step_name)
        
        # Identify inefficient steps
        inefficient_steps = []
        for step_name, efficiency in stats['step_efficiency'].items():
            eff_percent = float(efficiency['efficiency'].rstrip('%'))
            if eff_percent < 90:  # Less than 90% success rate
                inefficient_steps.append(step_name)
        
        if self.logger:
            if slow_steps:
                self.logger.log('warning', f'⚠️ Etapas lentas identificadas: {", ".join(slow_steps)}')
            if inefficient_steps:
                self.logger.log('warning', f'⚠️ Etapas ineficientes identificadas: {", ".join(inefficient_steps)}')
        
        return {
            'slow_steps': slow_steps,
            'inefficient_steps': inefficient_steps,
            'recommendations': self._generate_optimization_recommendations(slow_steps, inefficient_steps)
        }
    
    def _generate_optimization_recommendations(self, slow_steps: List[str], inefficient_steps: List[str]) -> List[str]:
        """Generate optimization recommendations"""
        recommendations = []
        
        if 'language_detection' in slow_steps:
            recommendations.append("Considerar cache de detecção de idioma por bloco")
        
        if 'content_validation' in slow_steps:
            recommendations.append("Unificar validações para evitar processamento duplicado")
        
        if 'translation' in slow_steps:
            recommendations.append("Implementar batch processing adaptativo")
        
        if 'quality_validation' in inefficient_steps:
            recommendations.append("Ajustar thresholds de validação de qualidade")
        
        if len(self.steps) > 6:
            recommendations.append("Considerar combinar etapas similares")
        
        return recommendations
    
    def reset_stats(self):
        """Reset all pipeline statistics"""
        with self.lock:
            self.pipeline_stats = {
                'total_executions': 0,
                'successful_executions': 0,
                'failed_executions': 0,
                'total_processing_time': 0.0,
                'avg_processing_time': 0.0,
                'step_stats': {step.value: {
                    'executions': 0,
                    'successes': 0,
                    'failures': 0,
                    'total_time': 0.0,
                    'avg_time': 0.0
                } for step, _ in self.steps}
            }
            
            if self.logger:
                self.logger.log('info', '📊 Estatísticas do pipeline resetadas')

class OptimizedTranslationPipeline(ProcessingPipeline):
    """
    Specialized pipeline for translation processing with optimizations
    """
    
    def __init__(self, logger=None, cache=None, thread_pool=None):
        super().__init__(logger)
        self.cache = cache
        self.thread_pool = thread_pool
        
        # Setup optimized pipeline
        self._setup_translation_pipeline()
    
    def _setup_translation_pipeline(self):
        """Setup the translation-specific pipeline"""
        # Step 1: Language Detection (with caching)
        self.add_step(ProcessingStep.LANGUAGE_DETECTION, self._cached_language_detection)
        
        # Step 2: Content Validation (unified)
        self.add_step(ProcessingStep.CONTENT_VALIDATION, self._unified_content_validation)
        
        # Step 3: Translation (with batch optimization)
        self.add_step(ProcessingStep.TRANSLATION, self._optimized_translation)
        
        # Step 4: Quality Validation (streamlined)
        self.add_step(ProcessingStep.QUALITY_VALIDATION, self._streamlined_quality_validation)
        
        # Step 5: Post Processing (minimal)
        self.add_step(ProcessingStep.POST_PROCESSING, self._minimal_post_processing)
        
        # Step 6: Cache Update (async)
        self.add_step(ProcessingStep.CACHE_UPDATE, self._async_cache_update)
    
    def _cached_language_detection(self, context: ProcessingContext) -> ProcessingContext:
        """Language detection with caching"""
        # This would integrate with the language detector module
        # For now, return context as-is (placeholder)
        return context
    
    def _unified_content_validation(self, context: ProcessingContext) -> ProcessingContext:
        """Unified content validation to avoid duplication"""
        # This would integrate with quality validator module
        # For now, return context as-is (placeholder)
        return context
    
    def _optimized_translation(self, context: ProcessingContext) -> ProcessingContext:
        """Optimized translation with batch processing"""
        # This would integrate with translator module
        # For now, return context as-is (placeholder)
        return context
    
    def _streamlined_quality_validation(self, context: ProcessingContext) -> ProcessingContext:
        """Streamlined quality validation"""
        # This would integrate with quality validator module
        # For now, return context as-is (placeholder)
        return context
    
    def _minimal_post_processing(self, context: ProcessingContext) -> ProcessingContext:
        """Minimal post-processing"""
        # Clean up and normalize the result
        if context.translated_text:
            context.translated_text = context.translated_text.strip()
            # Remove excessive whitespace
            context.translated_text = ' '.join(context.translated_text.split())
        
        return context
    
    def _async_cache_update(self, context: ProcessingContext) -> ProcessingContext:
        """Async cache update for better performance"""
        if self.cache and context.translated_text:
            # Update cache asynchronously if thread pool is available
            if self.thread_pool:
                self.thread_pool.submit_task(
                    self.cache.set,
                    context.original_text,
                    context.translated_text,
                    context.source_lang,
                    context.target_lang,
                    context.api_used
                )
            else:
                self.cache.set(
                    context.original_text,
                    context.translated_text,
                    context.source_lang,
                    context.target_lang,
                    context.api_used
                )
        
        return context

# Global pipeline instances for reuse
_global_pipeline = None
_pipeline_lock = threading.Lock()

def get_global_pipeline(logger=None, cache=None, thread_pool=None) -> OptimizedTranslationPipeline:
    """Get or create global pipeline instance"""
    global _global_pipeline
    
    if _global_pipeline is None:
        with _pipeline_lock:
            if _global_pipeline is None:
                _global_pipeline = OptimizedTranslationPipeline(logger, cache, thread_pool)
    
    return _global_pipeline

def cleanup_global_pipeline():
    """Cleanup global pipeline resources"""
    global _global_pipeline
    
    with _pipeline_lock:
        if _global_pipeline:
            _global_pipeline = None