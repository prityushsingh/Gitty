import argparse
from pathlib import Path
import sys


class Repository:
    def __init_(self, path = "."):
        self.path = Path(path).resolve()
        self.git_dir = self.path / ".gitty"

        #.gitty/objects
        self.objects_dir = self.git_dir / "objects"

        #.gitty/refs
        self/refs_dir = self.git_dir / "refs"

        #.gitty/HEAD
        self.head_file = self.git_dir / "HEAD"

        #.gitty/index
        self.index_file = self.git_dir / "index"




def main():
    parser = argparse.ArgumentParser(description="Gitty - My personal version control system")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # init command
    init_parser = subparsers.add_parser("init", help="Initialize a new repo")
    init_parser = subparsers.add_parser("add", help="add shit with this")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return
    try:
        pass
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

main()