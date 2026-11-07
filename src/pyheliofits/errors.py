class StudioNotRunning(RuntimeError):
    """No running Studio could be reached. The message says how to turn its API on."""


class CommandError(RuntimeError):
    """Studio refused or failed a command. `kind` is one of unknown-command, invalid-arguments,
    disabled, failed, declined (the user said no to a confirmation dialog)."""

    def __init__(self, kind: str, message: str):
        super().__init__(f"{kind}: {message}")
        self.kind = kind
        self.message = message
