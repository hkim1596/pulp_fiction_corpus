"""Coordinate live article replacement with complete human edit transactions."""
from contextlib import contextmanager
import fcntl
import hashlib
from pathlib import Path


@contextmanager
def article_write_lock(root, issue):
    """One lock per issue, shared by the website, publisher and upgrader.

    Hold it before reading an edit's baseline and until its final append, or
    across an automated replacement and any recovery. Never unlink lock files:
    waiting processes must continue locking the same inode.
    """
    directory = Path(root) / "data/corpus/article-write-locks"
    directory.mkdir(parents=True, exist_ok=True)
    name = hashlib.sha256(str(issue).encode("utf-8")).hexdigest() + ".lock"
    with (directory / name).open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def assembly_sha256(root, issue):
    path = Path(root) / "data/articles" / issue / "articles.json"
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return None


def upgrade_active(root):
    return (Path(root) / "data/corpus/CONTINUITY_UPGRADE").exists()
