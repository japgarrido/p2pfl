#
# This file is part of the federated_learning_p2p (p2pfl) distribution
# (see https://github.com/pguijas/federated_learning_p2p).
# Copyright (c) 2022 Pedro Guijas Bravo.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.
#

"""
P2PFL Logger.

.. note:: Not all is typed because the python logger is not typed (yep, is a TODO...).

"""

import atexit
import datetime
import logging
import os
import ray
from logging.handlers import RotatingFileHandler
from typing import Dict, List, Optional, Tuple

from p2pfl.management.metric_storage import GlobalLogsType, GlobalMetricStorage, LocalLogsType, LocalMetricStorage
from p2pfl.management.node_monitor import NodeMonitor
from p2pfl.management.p2pfl_web_services import P2pflWebServices
from p2pfl.node_state import NodeState, TrainingState
from p2pfl.settings import Settings

#########################################
#    Logging handler (transmit logs)    #
#########################################


class DictFormatter(logging.Formatter):
    """Formatter (logging) that returns a dictionary with the log record attributes."""

    def format(self, record):
        """
        Format the log record as a dictionary.

        Args:
            record: The log record.

        """
        # Get node
        if not hasattr(record, "node"):
            raise ValueError("The log record must have a 'node' attribute.")
        log_dict = {
            "timestamp": datetime.datetime.fromtimestamp(record.created),
            "level": record.levelname,
            "node": record.node,  # type: ignore
            "message": record.getMessage(),
        }
        return log_dict


class P2pflWebLogHandler(logging.Handler):
    """
    Custom logging handler that sends log entries to the API.

    Args:
        p2pfl_web: The P2PFL Web Services.

    """

    def __init__(self, p2pfl_web: P2pflWebServices):
        """Initialize the handler."""
        super().__init__()
        self.p2pfl_web = p2pfl_web
        self.formatter = DictFormatter()  # Instantiate the custom formatter

    def emit(self, record):
        """
        Emit the log record.

        Args:
            record: The log record.

        """
        # Format the log record using the custom formatter
        log_message = self.formatter.format(record)  # type: ignore
        # Send log entry to the API
        self.p2pfl_web.send_log(
            log_message["timestamp"],  # type: ignore
            log_message["node"],  # type: ignore
            log_message["level"],  # type: ignore
            log_message["message"],  # type: ignore
        )


#########################
#    Colored logging    #
#########################

# COLORS
GRAY = "\033[90m"
RED = "\033[91m"
YELLOW = "\033[93m"
GREEN = "\033[92m"
BLUE = "\033[94m"
CYAN = "\033[96m"
RESET = "\033[0m"


class ColoredFormatter(logging.Formatter):
    """Formatter that adds color to the log messages."""

    def format(self, record):
        """
        Format the log record with color.

        Args:
            record: The log record.

        """
        # Warn level color
        if record.levelname == "DEBUG":
            record.levelname = BLUE + record.levelname + RESET
        elif record.levelname == "INFO":
            record.levelname = GREEN + record.levelname + RESET
        elif record.levelname == "WARNING":
            record.levelname = YELLOW + record.levelname + RESET
        elif record.levelname == "ERROR" or record.levelname == "CRITICAL":
            record.levelname = RED + record.levelname + RESET
        return super().format(record)


###################
#    Ray Actor    #
###################


#class LoggerActor:
#    def __init__(self, p2pfl_web_services: Optional[P2pflWebServices] = None) -> None:
#        self.logger = Logger(p2pfl_web_services)

#    def get_logger(self) -> "Logger":
#        return self.logger
    


################
#    Logger    #
################

class ExperimentInfo:
    def __init__(self) -> None:
        self.experiment_name = None
        self.round = None

@ray.remote
class Logger:
    """
    Singleton class that manages the node logging.

    Keep in mind that the logs (with the exception of the console) are asynchronous.
    So if the program is closed abruptly, the logs may not be saved.

    Args:
        p2pfl_web_services: The P2PFL Web Services to log and monitor the nodes remotely.

    """

    ######
    # Singleton and instance management
    ######

    #__instance = None

    def connect_web(self, url: str, key: str) -> None:
        """
        Connect to the web services.

        Args:
            url: The URL of the web services.
            key: The API key.

        """
        # Remove the instance if it already exists
        #if Logger.__instance is not None:
        #    Logger.__instance.queue_listener.stop()
        #    Logger.__instance = None

        # Create the instance
        p2pfl_web = P2pflWebServices(url, key)

        # P2PFL Web Services
        self.p2pfl_web_services = p2pfl_web
        if p2pfl_web is not None:
            web_handler = P2pflWebLogHandler(p2pfl_web)
            self.logger.addHandler(web_handler)
        #Logger.__instance = Logger(p2pfl_web_services=p2pfl_web)

    def __init__(self, p2pfl_web_services: Optional[P2pflWebServices] = None) -> None:
        """Initialize the logger."""
        # Node States
        self.nodes: Dict[str, Tuple[Optional[NodeMonitor], ExperimentInfo]] = {}

        # Experiment Metrics
        self.local_metrics = LocalMetricStorage()
        self.global_metrics = GlobalMetricStorage()

        # Python logging
        self.logger = logging.getLogger("p2pfl")
        self.logger.propagate = False
        self.logger.setLevel(logging.getLevelName(Settings.LOG_LEVEL))
        handlers: List[logging.Handler] = []

        # P2PFL Web Services
        self.p2pfl_web_services = p2pfl_web_services
        if p2pfl_web_services is not None:
            web_handler = P2pflWebLogHandler(p2pfl_web_services)
            handlers.append(web_handler)

        # FILE - Handler
        if not os.path.exists(Settings.LOG_DIR):
            os.makedirs(Settings.LOG_DIR)
        file_handler = RotatingFileHandler(
            f"{Settings.LOG_DIR}/p2pfl.log", maxBytes=1000000, backupCount=3
        )  # TODO: ADD DIFFERENT LOG FILES FOR DIFFERENT NODES / EXPERIMENTS
        file_formatter = logging.Formatter(
            "[ %(asctime)s | %(node)s | %(levelname)s ]: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        file_handler.setFormatter(file_formatter)
        handlers.append(file_handler)

        # STDOUT - Handler
        stream_handler = logging.StreamHandler()
        cmd_formatter = ColoredFormatter(
            f"{GRAY}[ {YELLOW}%(asctime)s {GRAY}| {CYAN}%(node)s {GRAY}| %(levelname)s{GRAY} ]:{RESET} %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        stream_handler.setFormatter(cmd_formatter)
        self.logger.addHandler(stream_handler)  # not async

        # Asynchronous logging (queue handler)
        #self.log_queue: multiprocessing.Queue[logging.LogRecord] = multiprocessing.Queue()
        #queue_handler = QueueHandler(self.log_queue)
        #self.logger.addHandler(queue_handler)
        #self.queue_listener = QueueListener(self.log_queue, *handlers)
        #self.queue_listener.start()

        # Register cleanup function to close the queue on exit
        atexit.register(self.cleanup)

    def cleanup(self) -> None:
        """Cleanup the logger."""
        # Unregister nodes
        for node in self.nodes:
            self.unregister_node(node)

        # Stop the queue listener
        #if self.queue_listener:
        #    self.queue_listener.stop()

        # Remove handlers from the logger
        for handler in self.logger.handlers:
            self.logger.removeHandler(handler)

        # Close the multiprocessing queue
        #self.log_queue.close()

    ######
    # Application logging
    ######

    def set_level(self, level: int) -> None:
        """
        Set the logger level.

        Args:
            level: The logger level.

        """
        self.logger.setLevel(level)


    def get_level(self) -> int:
        """
        Get the logger level.

        Returns
            The logger level.

        """
        return self.logger.getEffectiveLevel()

    def get_level_name(self, lvl: int) -> str:
        """
        Get the logger level name.

        Args:
            lvl: The logger level.

        Returns:
            The logger level name.

        """
        return logging.getLevelName(lvl)

    def info(self, node: str, message: str) -> None:
        """
        Log an info message.

        Args:
            node: The node name.
            message: The message to log.

        """
        self.log(logging.INFO, node, message)

    def debug(self, node: str, message: str) -> None:
        """
        Log a debug message.

        Args:
            node: The node name.
            message: The message to log.

        """
        self.log(logging.DEBUG, node, message)

    def warning(self, node: str, message: str) -> None:
        """
        Log a warning message.

        Args:
            node: The node name.
            message: The message to log.

        """
        self.log(logging.WARNING, node, message)

    def error(self, node: str, message: str) -> None:
        """
        Log an error message.

        Args:
            node: The node name.
            message: The message to log.

        """
        self.log(logging.ERROR, node, message)

    def critical(self, node: str, message: str) -> None:
        """
        Log a critical message.

        Args:
            node: The node name.
            message: The message to log.

        """
        self.log(logging.CRITICAL, node, message)

    def log(self, level: int, node: str, message: str) -> None:
        """
        Log a message.

        Args:
            level: The log level.
            node: The node name.
            message: The message to log.

        """
        # Traditional logging
        if level == logging.DEBUG:
            self.logger.debug(message, extra={"node": node})
        elif level == logging.INFO:
            self.logger.info(message, extra={"node": node})
        elif level == logging.WARNING:
            self.logger.warning(message, extra={"node": node})
        elif level == logging.ERROR:
            self.logger.error(message, extra={"node": node})
        elif level == logging.CRITICAL:
            self.logger.critical(message, extra={"node": node})
        else:
            raise ValueError(f"Invalid level: {level}")

    ######
    # Metrics
    ######

    def log_metric(
        self,
        state: TrainingState,
        metric: str,
        value: float,
        step: Optional[int] = None,
    ) -> None:
        """
        Log a metric.

        Args:
            node: The node name.
            metric: The metric to log.
            value: The value.
            step: The step.
            round: The round.

        """
        # Get Round
        round = state.round if state.round is not None else self.nodes[state.addr][1].round
        if round is None:
            raise Exception("No round provided. Needed for training metrics.")
        
        self.nodes[state.addr][1].round = round

        # Get Experiment Name
        exp = state.experiment_name if state.experiment_name is not None else self.nodes[state.addr][1].experiment_name
        if exp is None:
            raise Exception("No experiment name provided. Needed for training metrics.")
        
        self.nodes[state.addr][1].experiment_name = exp

        # Local storage
        if step is None:
            # Global Metrics
            self.global_metrics.add_log(exp, state.round, metric, state.addr, value)
        else:
            # Local Metrics
            self.local_metrics.add_log(exp, state.round, metric, state.addr, value, step)

        # Web
        p2pfl_web_services = self.p2pfl_web_services
        if p2pfl_web_services is not None:
            if step is None:
                # Global Metrics
                p2pfl_web_services.send_global_metric(exp, state.round, metric, state.addr, value)
            else:
                # Local Metrics
                p2pfl_web_services.send_local_metric(exp, state.round, metric, state.addr, value, step)

    def log_system_metric(self, node: str, metric: str, value: float, time: datetime.datetime) -> None:
        """
        Log a system metric. Only on web.

        Args:
            node: The node name.
            metric: The metric to log.
            value: The value.
            time: The time.

        """
        # Web
        p2pfl_web_services = self.p2pfl_web_services
        if p2pfl_web_services is not None:
            p2pfl_web_services.send_system_metric(node, metric, value, time)

    def get_local_logs(self) -> LocalLogsType:
        """
        Get the logs.

        Args:
            node: The node name.
            exp: The experiment name.

        Returns:
            The logs.

        """
        return self.local_metrics.get_all_logs()

    def get_global_logs(self) -> GlobalLogsType:
        """
        Get the logs.

        Args:
            node: The node name.
            exp: The experiment name.

        Returns:
            The logs.

        """
        return self.global_metrics.get_all_logs()

    ######
    # Node registration
    ######

    def register_node(self, node: str, simulation: bool) -> None:
        """
        Register a node.

        Args:
            node: The node address.
            simulation: If the node is a simulation.

        """
        # Web
        node_monitor = None
        p2pfl_web_services = self.p2pfl_web_services
        if p2pfl_web_services is not None:
            # Register the node
            p2pfl_web_services.register_node(node, simulation)

            # Start the node status reporter
            node_monitor = NodeMonitor(node, self.log_system_metric)#NodeMonitor.remote(node, Logger.get_instance().log_system_metric)
            node_monitor.start()#.remote()

        # Node State
        if self.nodes.get(node) is None:
            # Dict[str, Tuple[NodeMonitor, ExperimentInfo]]
            self.nodes[node] = (node_monitor, ExperimentInfo())
        else:
            raise Exception(f"Node {node} already registered.")

    @staticmethod
    def unregister_node(self, node: str) -> None:
        """
        Unregister a node.

        Args:
            node: The node address.

        """
        # Web
        p2pfl_web_services = self.p2pfl_web_services
        if p2pfl_web_services is not None:
            p2pfl_web_services.unregister_node(node)

        # Node state
        n = self.nodes[node]
        if n is not None:
            # Stop the node status reporter
            if n[0] is not None:
                n[0].stop()
            # Unregister the node
            self.nodes.pop(node)
        else:
            raise Exception(f"Node {node} not registered.")
        

    ######
    # Node Status
    ######

    def experiment_started(self, node: str) -> None:
        """
        Notify the experiment start.

        Args:
            node: The node address.

        """
        self.warning(node, "Uncatched Experiment Started on Logger")

    def experiment_finished(self, node: str) -> None:
        """
        Notify the experiment end.

        Args:
            node: The node address.

        """
        self.warning(node, "Uncatched Experiment Ended on Logger")

    def round_finished(self, node: str) -> None:
        """
        Notify the round end.

        Args:
            node: The node address.

        """
        #r = self.nodes[node][1].round
        self.warning(node, f"Uncatched Round Finished on Logger")

# Logger actor singleton
logger = Logger.options(name="p2pfl_logger", lifetime="detached", get_if_exists=True).remote()