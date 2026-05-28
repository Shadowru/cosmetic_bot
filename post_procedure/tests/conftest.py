"""Shared pytest config: add the post_procedure dir to sys.path so tests can
`import bot, shorts_generator, config` без установки пакета.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
