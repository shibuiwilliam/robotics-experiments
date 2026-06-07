"""Allow `python -m agents` to run the smoke test."""

import asyncio

from agents.smoke import main

asyncio.run(main())
