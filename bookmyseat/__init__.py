# Makes sure the Celery app is loaded whenever Django starts, so that
# shared_task-decorated functions in movies/tasks.py always register.
from .celery import app as celery_app

__all__ = ('celery_app',)