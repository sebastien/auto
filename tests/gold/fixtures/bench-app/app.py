"""A tiny command-line greeter, the project the benchmark goals work on."""

import sys


def greet(name: str = "world") -> str:
	return f"Hello, {name}!"


if __name__ == "__main__":
	print(greet(*sys.argv[1:2]))
