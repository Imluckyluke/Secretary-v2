"""Importing this package registers all handlers on the shared Dispatcher."""

from secretary_bot.handlers import (  # noqa: F401
    membership,
    info,
    watch,
    business,
    edits,
    keywords,
    backup,
)
