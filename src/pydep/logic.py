from pathlib import Path
from typing import Callable

def find_resources(file: Path) -> list[Callable[[...], ...] | None]:
    """
    Find all resources in the given file.

    Args:
        file (Path): The path to the python file to search for resources.
    """
    resources = []
    with open(file, "r") as f:
        for line in f:
            if line.startswith("def "):
                ## extract used function code
                ...
            elif line.startswith("class "):
                ## extract used class code
                ...
            elif