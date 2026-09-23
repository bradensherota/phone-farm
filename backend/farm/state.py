"""Process-wide singletons shared by the API routers and the app lifecycle."""

from .devices.registry import manager
from .scheduler import Runner

runner = Runner(manager)

__all__ = ["manager", "runner"]
