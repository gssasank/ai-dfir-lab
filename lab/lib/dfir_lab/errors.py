"""Exit codes and the exceptions that carry a beginner-friendly message."""

EXIT_OK = 0        # success
EXIT_FAIL = 1      # a check failed or the answer was wrong
EXIT_USAGE = 2     # the command was typed wrongly
EXIT_CONFIG = 3    # configuration or installation problem


class LabError(Exception):
    """An expected problem. `what` says what happened, `todo` what to do next."""

    code = EXIT_FAIL

    def __init__(self, what, todo=None, code=None):
        super().__init__(what)
        self.what = what
        self.todo = todo
        if code is not None:
            self.code = code


class UsageError(LabError):
    code = EXIT_USAGE


class ConfigError(LabError):
    code = EXIT_CONFIG
