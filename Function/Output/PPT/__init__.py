# Copyright 2026 Zafer-Liu
# PFS 数据分析 Agent · 数据分析演示文稿生成框架
# Licensed under CC BY-NC 4.0 — see NOTICE.md and the applicable component terms.
#
"""McKinsey PPT Design Framework — High-level Layout Function Library.

Usage:
    from PPT import MckEngine
    eng = MckEngine(total_slides=30)
    eng.cover(title='My Title', subtitle='Subtitle')
    eng.toc(items=[('1','Topic','Description'), ...])
    eng.save('output/my_deck.pptx')
"""
from .engine import MckEngine
from .constants import *

__version__ = '2.3.0'
