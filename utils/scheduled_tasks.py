"""
Centralized scheduled task registration.

This module provides a single place to configure all scheduled tasks
while keeping the actual scheduling logic with the owning components.
"""
import logging
from typing import List, Tuple

logger = logging.getLogger(__name__)


# Registry of modules with scheduled tasks
# Format: (module_path, service_name, needs_init, init_args_factory)
SCHEDULED_TASK_MODULES: List[Tuple[str, str, bool, callable]] = [
    # Auth service - already has global instance
    # auth.service removed for OSS (single-user mode)

    # Segment timeout detection - registered separately (needs event_bus)
    # See register_segment_timeout_job() below
]



def initialize_all_scheduled_tasks(scheduler_service):
    """
    Initialize and register all scheduled tasks.

    Args:
        scheduler_service: The system scheduler service instance

    Returns:
        int: Number of successfully registered services

    Raises:
        RuntimeError: If any job registration fails
    """
    successful = 0

    # First register standard services from the registry
    for module_path, service_name, needs_init, init_args_factory in SCHEDULED_TASK_MODULES:
        try:
            # Dynamic import
            module = __import__(module_path, fromlist=[service_name])

            if needs_init:
                # Service needs initialization
                service_class = getattr(module, service_name)
                init_args = init_args_factory() if init_args_factory else {}
                service = service_class(**init_args)
            else:
                # Service is already a global instance
                service = getattr(module, service_name)

            # Register the service's scheduled jobs (raises on failure)
            if hasattr(service, 'register_cleanup_jobs'):
                service.register_cleanup_jobs(scheduler_service)
            elif hasattr(service, 'register_jobs'):
                service.register_jobs(scheduler_service)
            else:
                logger.warning(f"Service {service_name} has no job registration method")
                continue

            logger.info(f"Successfully registered scheduled tasks for {service_name}")
            successful += 1

        except Exception as e:
            logger.error(f"Error loading scheduled tasks from {module_path}: {e}", exc_info=True)
            raise RuntimeError(
                f"Failed to register scheduled tasks for {module_path}.{service_name}: {e}"
            ) from e

    # Register LT_Memory jobs using its special pattern
    try:
        from lt_memory.scheduled_tasks import register_lt_memory_jobs
        from lt_memory.factory import get_lt_memory_factory

        lt_memory_factory = get_lt_memory_factory()
        if not lt_memory_factory:
            raise RuntimeError("LT_Memory factory not initialized")

        if not register_lt_memory_jobs(scheduler_service, lt_memory_factory):
            raise RuntimeError("LT_Memory job registration returned False")

        logger.info("Successfully registered scheduled tasks for lt_memory")
        successful += 1

    except Exception as e:
        logger.error(f"Error registering LT_Memory scheduled tasks: {e}", exc_info=True)
        raise RuntimeError(f"Failed to register LT_Memory scheduled tasks: {e}") from e

    total_services = len(SCHEDULED_TASK_MODULES) + 1  # +1 for lt_memory
    logger.info(f"Scheduled task initialization complete: {successful}/{total_services} services registered")
    return successful


def register_segment_timeout_job(scheduler_service, event_bus) -> bool:
    """
    Register segment timeout detection job (called separately after event_bus initialization).

    Args:
        scheduler_service: System scheduler service
        event_bus: CNS event bus for publishing timeout events

    Returns:
        True if registered successfully

    Raises:
        RuntimeError: If job registration fails
    """
    try:
        from cns.services.segment_timeout_service import register_timeout_job

        success = register_timeout_job(scheduler_service, event_bus)
        if not success:
            raise RuntimeError("Segment timeout job registration returned False")

        logger.info("Successfully registered segment timeout detection job")
        return True

    except Exception as e:
        logger.error(f"Error registering segment timeout job: {e}", exc_info=True)
        raise RuntimeError(f"Failed to register segment timeout job: {e}") from e
