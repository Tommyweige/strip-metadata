"""Entry point for `python -m strip_metadata` and the packaged .exe.

No arguments (or files dropped on the .exe icon) opens the window;
`--cli ...` runs the command-line version.
"""
import sys


def main() -> int:
    args = sys.argv[1:]
    if args and args[0] == "--cli":
        from .cli import main as cli_main
        return cli_main(args[1:])
    from .gui import main as gui_main
    return gui_main(args)


if __name__ == "__main__":
    sys.exit(main())
