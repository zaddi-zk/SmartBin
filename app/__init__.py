"""Venice Check — honest card verification.

Package layout follows the build contract: core contracts first, adapters next,
Telegram shell last. Nothing in ``app.core`` imports the network, the database
or aiogram, so every rule in there is unit-testable in isolation.
"""

__version__ = "1.0.0"
