from abc import ABC, abstractmethod
from typing import Dict, Optional


class BaseProvider(ABC):
    """Abstract base class for all application providers."""

    def __init__(self, app_config: Dict):
        """
        Initialize the provider with its specific configuration.

        :param app_config: A dictionary containing the app's configuration
                           from the main application definition file (e.g., apps.yml).
        """
        self.app_config = app_config

    @abstractmethod
    async def initialize(self):
        """
        Perform any one-time initialization for the provider, such as
        pulling images or checking connectivity. This is called once at startup.
        """
        pass

    @abstractmethod
    async def launch(
        self,
        session_id: str,
        env_vars: Dict,
        volumes: Optional[Dict] = None,
        gpu_config: Optional[Dict] = None,
        is_collaboration: bool = False,
        master_token: Optional[str] = None,
        initial_tokens: Optional[Dict] = None,
    ) -> Dict:
        """
        Launch an instance of the application.

        :param session_id: The unique ID for this session.
        :param env_vars: Environment variables to pass to the instance.
        :param volumes: A dictionary defining volume mounts.
        :param gpu_config: A dictionary with GPU details if requested.
        :param is_collaboration: Flag indicating if this is a collaboration session.
        :param master_token: The master token for the downstream app's control plane.
        :param initial_tokens: The initial set of tokens to post to the downstream app.
        :return: A dictionary containing instance details, e.g.,
                 {'instance_id': '...', 'ip': '...', 'port': ...}.
        """
        pass

    @abstractmethod
    async def stop(self, instance_id: str):
        """
        Stop a running instance of the application.

        :param instance_id: The unique identifier of the instance to stop.
        """
        pass
