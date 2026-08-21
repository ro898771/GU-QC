import os

LONG_PATH_PREFIX = "\\\\?\\"   # this is the 4-character string:  \\?\


def long_path(path: str) -> str:
    r"""
    Return a path Windows can open even if it's longer than 260 characters.

    Windows blocks paths over MAX_PATH (260 chars) unless they start with
    the \\?\ marker, which tells it to skip the old path parser.
    That marker only works on absolute paths.
    """
    if path.startswith(LONG_PATH_PREFIX):
        return path                                  # already prefixed, leave it alone
    return LONG_PATH_PREFIX + os.path.abspath(path)  # make absolute, then prefix
