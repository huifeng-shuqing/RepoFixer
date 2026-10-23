"""Allow `python -m repofixer` to run the CLI."""
from .main import main

if __name__ == "__main__":
    raise SystemExit(main())
