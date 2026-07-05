import logging

logger = logging.getLogger(__name__)


class NginxReloader:
    """
    Nginx reload is handled automatically by the reloader sidecar
    container watching for ConfigMap changes via inotify.
    This class exists as an explicit hook for future override scenarios.
    """

    def trigger_reload(self) -> None:
        logger.info(
            "Nginx reload triggered — sidecar container will detect "
            "ConfigMap change and execute nginx -s reload automatically"
        )
