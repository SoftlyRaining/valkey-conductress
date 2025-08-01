"""Test full sync throughput"""

import datetime
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from src.replication_group import ReplicationGroup
from src.task_queue import BaseTaskData, BaseTaskRunner
from src.utility import HumanByte, HumanNumber, print_pretty_header, record_task_result


@dataclass
class SyncTaskData(BaseTaskData):
    test: str
    val_size: int
    val_count: int
    io_threads: int
    profiling_sample_rate: int

    def short_description(self) -> str:
        profiling = self.profiling_sample_rate > 0
        return (
            f"{HumanNumber.to_human(self.val_count)} {HumanByte.to_human(self.val_size)} {self.test} items, "
            f"{self.replicas} replica"
            f"{', profiling' if profiling else ''}"
        )

    def prepare_task_runner(self, server_ips: list[str]) -> "SyncTaskRunner":
        """Return the task runner for this task."""
        return SyncTaskRunner(
            f"{self.timestamp.strftime('%Y.%m.%d_%H.%M.%S.%f')}_{self.test}_sync",
            server_ips,
            self.source,
            self.specifier,
            io_threads=self.io_threads,
            valsize=self.val_size,
            valcount=self.val_count,
            profiling_sample_rate=self.profiling_sample_rate,
        )


class SyncTaskRunner(BaseTaskRunner):
    """Benchmark full sync throughput"""

    def __init__(
        self,
        task_name: str,
        server_ips: list,
        binary_source: str,
        specifier: str,
        io_threads: int,
        valsize: int,
        valcount: int,
        profiling_sample_rate: int,
    ):
        """Initialize the test with a replication group."""

        self.logger = logging.getLogger(self.__class__.__name__)

        self.task_name = task_name
        self.server_ips = server_ips
        self.binary_source = binary_source
        self.specifier = specifier
        self.io_threads = io_threads
        self.valsize = valsize
        self.valcount = valcount
        self.profiling_sample_rate = profiling_sample_rate

        assert len(self.server_ips) >= 2, "At least two server IPs are required"

        self.title = (
            f"replication test, {binary_source}:{specifier}, "
            f"io-threads={io_threads}, {HumanByte.to_human(valsize * valcount)} data, "
            f"{len(self.server_ips) - 1} replicas"
        )
        print_pretty_header(self.title)

        self.logger.info("setting up replication group with %d servers", len(self.server_ips))
        self.replication_group = ReplicationGroup(
            self.server_ips, self.binary_source, self.specifier, self.io_threads, []
        )

        self.logger.info("loading data onto primary")
        self.replication_group.primary.run_valkey_command_over_keyspace(
            self.valcount, f"-d {self.valsize} -t set"
        )

    def run(self) -> None:
        """Run the full sync test."""
        profiling = self.profiling_sample_rate > 0

        self.logger.info("beginning full sync...")
        self.replication_group.begin_replication()
        start = time.monotonic()

        if profiling:
            for server in self.replication_group.servers:
                server.profiling_start(self.profiling_sample_rate)

        in_sync = False
        while not in_sync:
            replica_info = self.replication_group.replicas[0].info("replication")
            in_sync = (
                "master_link_status" in replica_info
                and replica_info["master_link_status"] == "up"
                and replica_info["master_sync_in_progress"] == "0"
            )
            time.sleep(0.25)
        end = time.monotonic()
        self.logger.info("full sync complete")
        if profiling:
            for server in self.replication_group.servers:
                server.profiling_stop()

        duration = end - start
        user_data_size = self.valsize * self.valcount
        throughput = user_data_size / duration

        # record result
        result = {
            "sync_duration": end - start,
            "user_data_size": self.valsize * self.valcount,
            "throughput": user_data_size / duration,
        }
        record_task_result(
            "sync",
            self.binary_source,
            self.specifier,
            self.replication_group.primary.get_build_hash() or "",
            throughput,
            datetime.datetime.now(),
            result,
        )

        if profiling:
            self.__profiling_reports()

    def __profiling_reports(self):
        """This takes some time, so we have all hosts perform the task in parallel."""
        self.logger.info("generating flamegraphs")
        tasks = [
            (self.replication_group.primary, "primary"),
            *[(replica, f"replica{i}") for i, replica in enumerate(self.replication_group.replicas)],
        ]
        with ThreadPoolExecutor() as executor:
            executor.map(lambda t: t[0].profiling_report(self.task_name, t[1]), tasks)
        self.logger.info("flamegraphs done")
