from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
from typing import Dict, List, Optional, Tuple
import zlib


class GittyObject:
    """Generic git-like object (blob/tree/commit)"""

    def __init__(self, obj_type: str, content: bytes):
        self.type = obj_type
        self.content = content

    def hash(self) -> str:
        # f(<type> <size>\0<content>)
        header = f"{self.type} {len(self.content)}\0".encode()
        return hashlib.sha1(header + self.content).hexdigest()

    def serialize(self) -> bytes:
        header = f"{self.type} {len(self.content)}\0".encode()
        return zlib.compress(header + self.content)

    @classmethod
    def deserialize(cls, data: bytes) -> "GittyObject":
        decompressed = zlib.decompress(data)
        null_idx = decompressed.find(b"\0")
        header = decompressed[:null_idx].decode()
        content = decompressed[null_idx + 1 :]

        obj_type, _ = header.split(" ", 1)
        return cls(obj_type, content)


class GittyBlob(GittyObject):
    def __init__(self, content: bytes):
        super().__init__("blob", content)


class GittyTree(GittyObject):
    """
    Tree stores entries as tuples: (mode, name, hash)
    On-disk format: multiple entries of "<mode> <name>\0<20 raw bytes of hash>"
    """

    def __init__(self, entries: Optional[List[Tuple[str, str, str]]] = None):
        self.entries = entries or []
        content = self._serialize_entries()
        super().__init__("tree", content)

    def _serialize_entries(self) -> bytes:
        content = b""
        # sort by name to have deterministic order (mode,name,obj_hash)
        for mode, name, obj_hash in sorted(self.entries, key=lambda e: (e[1], e[0])):
            content += f"{mode} {name}\0".encode()
            # obj_hash stored as 20 raw bytes
            content += bytes.fromhex(obj_hash)
        return content

    def add_entry(self, mode: str, name: str, obj_hash: str):
        self.entries.append((mode, name, obj_hash))
        self.content = self._serialize_entries()

    @classmethod
    def from_content(cls, content: bytes) -> "GittyTree":
        tree = cls()
        i = 0
        n = len(content)

        while i < n:
            null_idx = content.find(b"\0", i)
            if null_idx == -1:
                break
            mode_name = content[i:null_idx].decode()
            # mode and name split by first space
            mode, name = mode_name.split(" ", 1)
            # next 20 bytes are raw SHA1 bytes
            obj_hash = content[null_idx + 1 : null_idx + 21].hex()
            tree.entries.append((mode, name, obj_hash))
            i = null_idx + 21

        return tree


class GittyCommit(GittyObject):
    """
    Commit content format:
        tree <tree_hash>
        parent <parent_hash>   (0 or more)
        author <author> <timestamp> +0000
        committer <committer> <timestamp> +0000
        <blank line>
        <message...>
    """

    def __init__(
        self,
        tree_hash: str,
        parent_hashes: List[str],
        author: str,
        committer: str,
        message: str,
        timestamp: Optional[int] = None,
    ):
        self.tree_hash = tree_hash
        self.parent_hashes = parent_hashes
        self.author = author
        self.committer = committer
        self.message = message
        self.timestamp = timestamp or int(time.time())

        content = self._serialize_commit()
        super().__init__("commit", content)

    def _serialize_commit(self) -> bytes:
        lines = [f"tree {self.tree_hash}"]
        for parent in self.parent_hashes:
            lines.append(f"parent {parent}")

        lines.append(f"author {self.author} {self.timestamp} +0000")
        lines.append(f"committer {self.committer} {self.timestamp} +0000")
        lines.append("")
        lines.append(self.message or "")
        return "\n".join(lines).encode()

    @classmethod
    def from_content(cls, content: bytes) -> "GittyCommit":
        lines = content.decode().split("\n")
        tree_hash = None
        parent_hashes: List[str] = []
        author = None
        committer = None
        timestamp = None
        message_start = 0

        for i, line in enumerate(lines):
            if line.startswith("tree "):
                tree_hash = line[5:]
            elif line.startswith("parent "):
                parent_hashes.append(line[7:])
            elif line.startswith("author "):
                # author line format: "<author> <timestamp> +0000"
                # split from right to avoid splitting author name/email with spaces
                parts = line[7:].rsplit(" ", 2)
                if len(parts) >= 2:
                    author = parts[0]
                    try:
                        timestamp = int(parts[1])
                    except Exception:
                        timestamp = None
            elif line.startswith("committer "):
                parts = line[10:].rsplit(" ", 2)
                if len(parts) >= 1:
                    committer = parts[0]
            elif line == "":
                message_start = i + 1
                break

        message = "\n".join(lines[message_start:]) if message_start < len(lines) else ""
        # if timestamp not parsed, set to current time
        timestamp = timestamp or int(time.time())
        return cls(
            tree_hash=tree_hash,
            parent_hashes=parent_hashes,
            author=author or "",
            committer=committer or "",
            message=message,
            timestamp=timestamp,
        )


class Repository:
    def __init__(self, path: str = "."):
        self.path = Path(path).resolve()
        self.gitty_dir = self.path / ".gitty"

        # .gitty/objects
        self.objects_dir = self.gitty_dir / "objects"

        # .gitty/refs and heads
        self.ref_dir = self.gitty_dir / "refs"
        self.heads_dir = self.ref_dir / "heads"

        # HEAD file
        self.head_file = self.gitty_dir / "HEAD"

        # .gitty/index
        self.index_file = self.gitty_dir / "index"

    def init(self) -> bool:
        if self.gitty_dir.exists():
            return False

        # create directories
        self.gitty_dir.mkdir(parents=True)
        self.objects_dir.mkdir(parents=True)
        self.ref_dir.mkdir(parents=True)
        self.heads_dir.mkdir(parents=True)

        # create initial HEAD pointing to a branch
        self.head_file.write_text("ref: refs/heads/master\n")

        # create empty index
        self.save_index({})

        print(f"Initialized empty Gitty repository in {self.gitty_dir}")
        return True

    def store_object(self, obj: GittyObject) -> str:
        obj_hash = obj.hash()
        obj_dir = self.objects_dir / obj_hash[:2]
        obj_file = obj_dir / obj_hash[2:]

        if not obj_file.exists():
            obj_dir.mkdir(exist_ok=True, parents=True)
            obj_file.write_bytes(obj.serialize())

        return obj_hash

    def load_index(self) -> Dict[str, str]:
        if not self.index_file.exists():
            return {}
        try:
            return json.loads(self.index_file.read_text())
        except Exception:
            return {}

    def save_index(self, index: Dict[str, str]):
        self.index_file.write_text(json.dumps(index, indent=2))

    def add_file(self, path: str):
        full_path = self.path / path
        if not full_path.exists():
            raise FileNotFoundError(f"Path {path} not found")
        if ".gitty" in full_path.parts:
            return  # don't add internal files

        # Read the file content
        content = full_path.read_bytes()
        # Create BLOB object from the content
        blob = GittyBlob(content)
        # store the blob object in database (.gitty/objects)
        blob_hash = self.store_object(blob)
        # Update index to include the file
        index = self.load_index()
        index[path] = blob_hash
        self.save_index(index)

        print(f"Added {path}")

    def add_directory(self, path: str):
        full_path = self.path / path
        if not full_path.exists():
            raise FileNotFoundError(f"Directory {path} not found")
        if not full_path.is_dir():
            raise ValueError(f"{path} is not a directory")
        index = self.load_index()
        added_count = 0
        # recursively traverse the directory
        for file_path in full_path.rglob("*"):
            if file_path.is_file():
                if ".gitty" in file_path.parts:
                    continue

                # create & store blob object
                content = file_path.read_bytes()
                blob = GittyBlob(content)
                blob_hash = self.store_object(blob)
                # update index
                rel_path = str(file_path.relative_to(self.path))
                index[rel_path] = blob_hash
                added_count += 1

        self.save_index(index)

        if added_count > 0:
            print(f"Added {added_count} files from directory {path}")
        else:
            print(f"Directory {path} already up to date")

    def add_path(self, path: str) -> None:
        full_path = self.path / path

        if not full_path.exists():
            raise FileNotFoundError(f"Path {path} not found")

        if full_path.is_file():
            self.add_file(path)
        elif full_path.is_dir():
            self.add_directory(path)
        else:
            raise ValueError(f"{path} is neither a file nor a directory")

    def load_object(self, obj_hash: str) -> GittyObject:
        obj_dir = self.objects_dir / obj_hash[:2]
        obj_file = obj_dir / obj_hash[2:]

        if not obj_file.exists():
            raise FileNotFoundError(f"Object {obj_hash} not found")

        return GittyObject.deserialize(obj_file.read_bytes())

    def create_tree_from_index(self) -> str:
        index = self.load_index()
        if not index:
            tree = GittyTree()
            return self.store_object(tree)

        # Build nested dict structure for directories x
        dirs: Dict[str, Dict] = {}
        files: Dict[str, str] = {}

        for file_path, blob_hash in index.items():
            parts = file_path.split("/")

            if len(parts) == 1:
                # file in root
                files[parts[0]] = blob_hash
            else:
                dir_name = parts[0]
                if dir_name not in dirs:
                    dirs[dir_name] = {}

                current = dirs[dir_name]
                for part in parts[1:-1]:
                    if part not in current:
                        current[part] = {}
                    current = current[part]

                current[parts[-1]] = blob_hash

        def create_tree_recursive(entries_dict: Dict) -> str:
            tree = GittyTree()
            for name, val in entries_dict.items():
                if isinstance(val, str):
                    # a blob
                    tree.add_entry("100644", name, val)
                elif isinstance(val, dict):
                    subtree_hash = create_tree_recursive(val)
                    tree.add_entry("40000", name, subtree_hash)
            return self.store_object(tree)

        # Merge files into root entries and then include top-level dirs
        root_entries = {**files}
        for dir_name, dir_contents in dirs.items():
            root_entries[dir_name] = dir_contents

        return create_tree_recursive(root_entries)

    def get_current_branch(self) -> str:
        if not self.head_file.exists():
            return "master"

        head_content = self.head_file.read_text().strip()
        if head_content.startswith("ref: refs/heads/"):
            return head_content[16:]
        return "HEAD"  # detached HEAD

    def get_branch_commit(self, current_branch: str) -> Optional[str]:
        branch_file = self.heads_dir / current_branch

        if branch_file.exists():
            return branch_file.read_text().strip()
        return None

    def set_branch_commit(self, current_branch: str, commit_hash: str):
        branch_file = self.heads_dir / current_branch
        # ensure heads dir exists
        branch_file.parent.mkdir(parents=True, exist_ok=True)
        branch_file.write_text(commit_hash + "\n")

    def commit(
        self,
        message: str,
        author: str = "Gitty User <user@gitty.local>",
    ) -> Optional[str]:
        # create a tree object from the index (staging area)
        tree_hash = self.create_tree_from_index()

        current_branch = self.get_current_branch()
        parent_commit = self.get_branch_commit(current_branch)
        parent_hashes = [parent_commit] if parent_commit else []

        index = self.load_index()
        if not index:
            print("nothing to commit, working tree clean")
            return None

        if parent_commit:
            parent_git_commit_obj = self.load_object(parent_commit)
            parent_commit_data = GittyCommit.from_content(parent_git_commit_obj.content)
            if tree_hash == parent_commit_data.tree_hash:
                print("nothing to commit, working tree clean")
                return None

        commit = GittyCommit(
            tree_hash=tree_hash,
            parent_hashes=parent_hashes,
            author=author,
            committer=author,
            message=message,
        )
        commit_hash = self.store_object(commit)

        self.set_branch_commit(current_branch, commit_hash)
        self.save_index({})
        print(f"Created commit {commit_hash} on branch {current_branch}")
        return commit_hash

    def get_files_from_tree_recursive(
        self,
        tree_hash: str,
        prefix: str = "",
    ) -> set:
        files = set()
        try:
            tree_obj = self.load_object(tree_hash)
            tree = GittyTree.from_content(tree_obj.content)
            for mode, name, obj_hash in tree.entries:
                full_name = f"{prefix}{name}"
                if mode.startswith("100"):
                    files.add(full_name)
                elif mode.startswith("400"):
                    subtree_files = self.get_files_from_tree_recursive(
                        obj_hash, f"{full_name}/"
                    )
                    files.update(subtree_files)
        except Exception as e:
            print(f"Warning: Could not read tree {tree_hash}: {e}")

        return files

    def checkout(self, branch: str, create_branch: bool):
        # computed the files to clear from the previous branch
        previous_branch = self.get_current_branch()
        files_to_clear = set()
        previous_commit_hash = None
        try:
            previous_commit_hash = self.get_branch_commit(previous_branch)
            if previous_commit_hash:
                prev_commit_object = self.load_object(previous_commit_hash)
                prev_commit = GittyCommit.from_content(prev_commit_object.content)
                if prev_commit.tree_hash:
                    files_to_clear = self.get_files_from_tree_recursive(
                        prev_commit.tree_hash
                    )
        except Exception:
            files_to_clear = set()

        # created/moved to a new branch
        branch_file = self.heads_dir / branch
        if not branch_file.exists():
            if create_branch:
                if previous_commit_hash:
                    self.set_branch_commit(branch, previous_commit_hash)
                    print(f"Created new branch {branch}")
                else:
                    print("No commits yet, cannot create a branch")
                    return
            else:
                print(f"Branch '{branch}' not found.")
                print(
                    "Use 'python3 main.py checkout -b {branch}' to create and switch to a new branch."
                )
                return
        # update HEAD to point to this branch
        self.head_file.write_text(f"ref: refs/heads/{branch}\n")

        # restore working directory for the target branch (and remove old files)
        self.restore_working_directory(branch, files_to_clear)
        print(f"Switched to branch {branch}")

    def restore_tree(self, tree_hash: str, path: Path):
        tree_obj = self.load_object(tree_hash)
        tree = GittyTree.from_content(tree_obj.content)
        for mode, name, obj_hash in tree.entries:
            file_path = path / name
            if mode.startswith("100"):
                blob_obj = self.load_object(obj_hash)
                blob = GittyBlob(blob_obj.content)
                # ensure parent dir exists
                file_path.parent.mkdir(parents=True, exist_ok=True)
                file_path.write_bytes(blob.content)
            elif mode.startswith("400"):
                file_path.mkdir(exist_ok=True)
                self.restore_tree(obj_hash, file_path)

    def restore_working_directory(
        self,
        branch: str,
        files_to_clear: set,
    ):
        target_commit_hash = self.get_branch_commit(branch)
        if not target_commit_hash:
            # nothing to restore
            return

        # remove files tracked by previous branch
        for rel_path in sorted(files_to_clear):
            file_path = self.path / rel_path
            try:
                if file_path.is_file():
                    file_path.unlink()
            except Exception:
                pass

        target_commit_obj = self.load_object(target_commit_hash)
        target_commit = GittyCommit.from_content(target_commit_obj.content)

        if target_commit.tree_hash:
            self.restore_tree(target_commit.tree_hash, self.path)

        # reset index to match checked out commit
        self.save_index({})

    def branch(self, branch_name: Optional[str], delete: bool = False):
        # delete branch
        if delete and branch_name:
            branch_file = self.heads_dir / branch_name
            if branch_file.exists():
                branch_file.unlink()
                print(f"Deleted branch {branch_name}")
            else:
                print(f"Branch {branch_name} not found")
            return

        current_branch = self.get_current_branch()
        if branch_name:
            current_commit = self.get_branch_commit(current_branch)
            if current_commit:
                self.set_branch_commit(branch_name, current_commit)
                print(f"Created branch {branch_name}")
            else:
                print(f"No commits yet, cannot create a new branch")
        else:
            branches = []
            if self.heads_dir.exists():
                for branch_file in self.heads_dir.iterdir():
                    if branch_file.is_file() and not branch_file.name.startswith("."):
                        branches.append(branch_file.name)

            for branch in sorted(branches):
                current_marker = "* " if branch == current_branch else "  "
                print(f"{current_marker}{branch}")

    def log(self, max_count: int = 10):
        current_branch = self.get_current_branch()
        commit_hash = self.get_branch_commit(current_branch)

        if not commit_hash:
            print("No commits yet!")
            return

        count = 0
        while commit_hash and count < max_count:
            commit_obj = self.load_object(commit_hash)
            commit = GittyCommit.from_content(commit_obj.content)

            print(f"commit {commit_hash}")
            print(f"Author: {commit.author}")
            print(f"Date: {time.ctime(commit.timestamp)}")
            print(f"\n    {commit.message}\n")

            commit_hash = commit.parent_hashes[0] if commit.parent_hashes else None
            count += 1

    def build_index_from_tree(self, tree_hash: str, prefix: str = "") -> Dict[str, str]:
        index: Dict[str, str] = {}
        try:
            tree_obj = self.load_object(tree_hash)
            tree = GittyTree.from_content(tree_obj.content)
            for mode, name, obj_hash in tree.entries:
                full_name = f"{prefix}{name}"
                if mode.startswith("100"):
                    index[full_name] = obj_hash
                elif mode.startswith("400"):
                    subindex = self.build_index_from_tree(obj_hash, f"{full_name}/")
                    index.update(subindex)
        except Exception as e:
            print(f"Warning: Could not read tree {tree_hash}: {e}")
        return index

    def get_all_files(self) -> List[Path]:
        files: List[Path] = []
        for item in self.path.rglob("*"):
            if ".gitty" in item.parts:
                continue
            if item.is_file():
                files.append(item)
        return files

    def status(self):
        # what branch we are on
        current_branch = self.get_current_branch()
        print(f"On branch {current_branch}")
        index = self.load_index()
        current_commit_hash = self.get_branch_commit(current_branch)

        # build the index of the latest commit
        last_index_files: Dict[str, str] = {}
        if current_commit_hash:
            try:
                commit_obj = self.load_object(current_commit_hash)
                commit = GittyCommit.from_content(commit_obj.content)
                if commit.tree_hash:
                    last_index_files = self.build_index_from_tree(commit.tree_hash)
            except Exception:
                last_index_files = {}

        # figure out all the files present within the working directory
        working_files: Dict[str, str] = {}
        for item in self.get_all_files():
            rel_path = str(item.relative_to(self.path))
            try:
                content = item.read_bytes()
                blob = GittyBlob(content)
                working_files[rel_path] = blob.hash()
            except Exception:
                continue

        staged_files = []
        unstaged_files = []
        untracked_files = []
        deleted_files = []

        # what files are staged for commit
        for file_path in set(index.keys()) | set(last_index_files.keys()):
            index_hash = index.get(file_path)
            last_index_hash = last_index_files.get(file_path)

            if index_hash and not last_index_hash:
                staged_files.append(("new file", file_path))
            elif index_hash and last_index_hash and index_hash != last_index_hash:
                staged_files.append(("modified", file_path))

        if staged_files:
            print("\nChanges to be committed:")
            for stage_status, file_path in sorted(staged_files):
                print(f"   {stage_status}: {file_path}")

        # what files have modified but not staged
        for file_path in working_files:
            if file_path in index:
                if working_files[file_path] != index[file_path]:
                    unstaged_files.append(file_path)

        if unstaged_files:
            print("\nChanges not staged for commit:")
            for file_path in sorted(unstaged_files):
                print(f"   modified: {file_path}")

        # what files are untracked
        for file_path in working_files:
            if file_path not in index and file_path not in last_index_files:
                untracked_files.append(file_path)

        if untracked_files:
            print("\nUntracked files:")
            for file_path in sorted(untracked_files):
                print(f"   {file_path}")

        # what files have been deleted
        for file_path in index:
            if file_path not in working_files:
                deleted_files.append(file_path)

        if deleted_files:
            print("\nDeleted files:")
            for file_path in sorted(deleted_files):
                print(f"   deleted: {file_path}")

        if (
            not staged_files
            and not unstaged_files
            and not deleted_files
            and not untracked_files
        ):
            print("\nnothing to commit, working tree clean")


def main():
    parser = argparse.ArgumentParser(description="Gitty - A simple git clone (using .gitty folder)")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # init command
    subparsers.add_parser("init", help="Initialize a new repository")

    # add command
    add_parser = subparsers.add_parser(
        "add", help="Add files and directories to the staging area"
    )
    add_parser.add_argument("paths", nargs="+", help="Files and directories to add")

    # commit command
    commit_parser = subparsers.add_parser("commit", help="Create a new commit")
    commit_parser.add_argument(
        "-m",
        "--message",
        help="Commit message",
        required=True,
    )
    commit_parser.add_argument(
        "--author",
        help="Author name and email",
    )

    # checkout command
    checkout_parser = subparsers.add_parser("checkout", help="Move/Create a new branch")
    checkout_parser.add_argument("branch", help="Branch to switch to")
    checkout_parser.add_argument(
        "-b",
        "--create-branch",
        action="store_true",
        help="Create and switch to a new branch",
    )

    # branch command
    branch_parser = subparsers.add_parser("branch", help="List or manage branches")
    branch_parser.add_argument("name", nargs="?")
    branch_parser.add_argument(
        "-d",
        "--delete",
        action="store_true",
        help="Delete the branch",
    )

    # log command
    log_parser = subparsers.add_parser("log", help="Show commit history")
    log_parser.add_argument(
        "-n",
        "--max-count",
        type=int,
        default=10,
        help="Limit commits shown",
    )

    # status command
    subparsers.add_parser("status", help="Show repository status")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    repo = Repository()
    try:
        if args.command == "init":
            if not repo.init():
                print("Repository already exists")
                return
        elif args.command == "add":
            if not repo.gitty_dir.exists():
                print("Not a Gitty repository")
                return

            for path in args.paths:
                repo.add_path(path)
        elif args.command == "commit":
            if not repo.gitty_dir.exists():
                print("Not a Gitty repository")
                return

            author = args.author or "Gitty user <user@gitty.local>"
            repo.commit(args.message, author)
        elif args.command == "checkout":
            if not repo.gitty_dir.exists():
                print("Not a Gitty repository")
                return
            repo.checkout(args.branch, args.create_branch)
        elif args.command == "branch":
            if not repo.gitty_dir.exists():
                print("Not a Gitty repository")
                return

            repo.branch(args.name, args.delete)
        elif args.command == "log":
            if not repo.gitty_dir.exists():
                print("Not a Gitty repository")
                return

            repo.log(args.max_count)
        elif args.command == "status":
            if not repo.gitty_dir.exists():
                print("Not a Gitty repository")
                return

            repo.status()
        else:
            print(f"Unknown command: {args.command}")

    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
