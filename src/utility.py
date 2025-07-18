"""Misc utility functions: Printing, formatting, command execution, etc."""

import json
import os
import signal
import subprocess
from datetime import datetime
from typing import Optional, Sequence, Union

from .config import CONDUCTRESS_DATA_DUMP, CONDUCTRESS_OUTPUT, SSH_KEYFILE

MILLION = 1_000_000
BILLION = 1_000_000_000

MB = 1024 * 1024
GB = 1024 * MB

MINUTE = 60


def get_console_width() -> int:
    """Determine the width of the console."""
    try:
        return os.get_terminal_size().columns
    except OSError:
        return 80


class HumanNumber:
    base: Union[int, Sequence[int]] = 1000
    units: Sequence = ("", "K", "M", "G", "T", "P", "E", "Z", "Y")
    threshold = 0.5  # Switch to next unit once result in that unit is >= threshold.

    @classmethod
    def to_human(cls, number: float, decimals: int = 1) -> str:
        """
        Convert number to a human-readable format with appropriate units.

        Args:
            number (float): number to convert
            base (Union[int, tuple]): base for conversion - int if constant divisor between units (e.g. 1000),
                or tuple of bases for each unit if non-constant (as in time)
            units (tuple): sequence of unit suffixes
            decimals (int): decimals places to show
            threshold (float, optional): Switches to next unit once result in that unit is >= threshold.

        Returns:
            str: formatted string
        """
        number = float(number)
        unit_index = 0
        if isinstance(cls.base, int):
            while number >= cls.base * cls.threshold and unit_index + 1 < len(cls.units):
                unit_index += 1
                number /= cls.base
        else:
            bases: Sequence[int] = cls.base
            assert len(bases) == len(cls.units)
            unit_index = 0
            while unit_index + 1 < len(bases) and number >= bases[unit_index + 1] * cls.threshold:
                unit_index += 1
                number /= bases[unit_index]

        if number.is_integer():
            return f"{number:,g}{cls.units[unit_index]}"
        else:
            return f"{number:,.{decimals}f}{cls.units[unit_index]}"

    @classmethod
    def from_human(cls, human_string: str) -> float:
        """
        Convert a human-readable number with units back to a float.

        Args:
            input (str): input string with number and unit
            base (Union[int, tuple]): base for conversion - int if constant divisor between units (e.g. 1000),
                or tuple of bases for each unit if non-constant (as in time)
            units (tuple): sequence of unit suffixes

        Returns:
            float: converted number
        """
        human_string = human_string.strip()
        index = len(human_string)
        while index > 0 and human_string[index - 1].isalpha():
            index -= 1
        unit = human_string[index:].lower()
        number = float(human_string[:index])
        if unit == "":
            return number
        lower_units = (x.lower() for x in cls.units)
        if isinstance(cls.base, int):
            for unit_suffix in lower_units:
                if unit == unit_suffix:
                    return number
                number *= cls.base
        else:
            assert len(cls.base) == len(cls.units)
            for base, unit_suffix in zip(cls.base, lower_units):
                print(number, base, unit_suffix)
                number *= base
                if unit == unit_suffix:
                    return number
        raise ValueError(f"Invalid unit '{unit}'. Expected one of {cls.units} (case insensitive).")


class HumanByte(HumanNumber):
    base = 1024
    units = ("B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB")


class HumanTime(HumanNumber):
    base = (1, 60, 60, 24, 7)
    units = ("s", "m", "h", "d", "w")
    threshold = 1


def __build_header(left_decor: str, center: str, right_decor: str, fill: str = "─") -> str:
    console_width = get_console_width()

    # Decorative elements
    left_decor = "⊹˚₊‧"
    right_decor = "‧₊˚⊹"
    fill = "─"

    # Calculate padding needed
    content_len = len(center) + 2  # +2 for spaces around text
    decor_len = len(left_decor) + len(right_decor)
    fill_len = console_width - content_len - decor_len

    # Split fill chars evenly on each side
    fill_str = fill * (fill_len // 2)

    # Construct and print the header
    header = f"{left_decor}{fill_str} {center} {fill_str}{right_decor}"
    return header


def print_inline_header(text: str):
    """Print decorative header with inline centered text"""
    print(__build_header("⊹˚₊‧", text, "‧₊˚⊹", "─"))


def print_pretty_divider():
    """Print a decorative header (no text)"""
    print(__build_header("⊹˚₊‧", "•°•♥•°•", "‧₊˚⊹", "─"))


def print_centered_text(text: str):
    """Print text centered in the console."""
    console_width = get_console_width()
    textlen = len(text)
    padding = (console_width - textlen) // 2
    text = " " * padding + text + " " * (console_width - textlen - padding)
    print(text)


def print_pretty_header(text: str):
    """Print a header with a nice divider."""
    print_pretty_divider()
    print_centered_text(text)


def calc_percentile_averages(data: list, percentages, lowest_vals=False) -> list[float]:
    """Calculate the average of the top or bottom x percents of values in a list."""
    copy = data.copy()
    copy.sort()
    if lowest_vals:
        copy.reverse()

    result = []
    for percent in percentages:
        start_index = len(copy) * (100 - percent) // 100
        section = copy[start_index:]
        section_avg = sum(section) / len(section)
        result.append(section_avg)
    return result


def record_task_result(
    method: str, source: str, specifier: str, commit_hash: str, score: float, endtime: datetime, data
) -> None:
    """
    Uniform method for recording results

    Args:
        method (str): task type (perf-get, mem, sync, etc)
        source (str): usually the repo name
        specifier (str): branch name, tag, or hash
        commit_hash (str): actual commit used
        score (float): summarize result in 1 key number
        endtime (datetime): timestamp for task completion
        data (_type_): more detailed result info - format depends on the task
    """
    result = {
        "method": method,
        "source": source,
        "specifier": specifier,
        "commit_hash": commit_hash,
        "score": score,
        "end_time": str(endtime),
        "data": data,
    }
    with open(CONDUCTRESS_OUTPUT, "a", encoding="utf-8") as f:
        f.write(json.dumps(result))
        f.write("\n")


def dump_task_data(method: str, commit_hash: str, endtime: datetime, data):
    """
    Uniform method for dumping all data collected

    Args:
        method (str): task type (perf-get, mem, sync, etc)
        commit_hash (str): actual commit used
        endtime (datetime): timestamp for task completion
        data (_type_): All data recorded - format depends on the task
    """
    result = {
        "method": method,
        "commit_hash": commit_hash,
        "end_time": str(endtime),
        "data": data,
    }
    with open(CONDUCTRESS_DATA_DUMP, "a", encoding="utf-8") as f:
        f.write(json.dumps(result))
        f.write("\n")


class RealtimeCommand:
    """Run a local command in real-time and read its output."""

    def __init__(self, command: str, remote: Optional[str] = None):
        self.command = command
        self.remote = remote
        self.p = None

    def __del__(self):
        """Destructor - kill the process if it's still running."""
        self.kill()

    def start(self, ignore_output=False):
        """Start the command in a subprocess."""
        if self.p is not None:
            raise RuntimeError("Command already running")
        command_list = self.command.split()
        if self.remote is not None:
            command_list = ["ssh", "-q", "-i", SSH_KEYFILE, self.remote] + command_list
        output_dest = subprocess.DEVNULL if ignore_output else subprocess.PIPE
        self.p = subprocess.Popen(command_list, stdout=output_dest, stderr=output_dest)
        if not ignore_output:
            assert self.p.stdout is not None
            assert self.p.stderr is not None
            os.set_blocking(self.p.stdout.fileno(), False)
            os.set_blocking(self.p.stderr.fileno(), False)

    def poll_output(self) -> tuple[str, str]:
        """Read output since last poll."""
        if self.p is None:
            raise RuntimeError("Command not started")
        assert self.p.stdout is not None
        assert self.p.stderr is not None
        stdout = self.p.stdout.read()
        stderr = self.p.stderr.read()
        if stdout is not None:
            stdout = stdout.decode("utf-8")
        if stderr is not None:
            stderr = stderr.decode("utf-8")
        return stdout, stderr

    def is_running(self):
        """Check if the process is still running."""
        if self.p is None:
            return False  # process not started
        if self.p.poll() is not None:
            return False  # process finished
        return True

    def interrupt(self):
        """Send interrupt signal to process"""
        if self.p and self.p.poll() is None:
            self.p.send_signal(signal.SIGINT)

    def wait(self):
        """Block until process has ended."""
        if self.p:
            self.p.wait()

    def kill(self):
        """Kill the procress."""
        if self.p:
            self.p.kill()
            self.p.wait()
