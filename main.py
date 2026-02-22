#!/usr/bin/env python3
"""
Sonarr Subtitle Translator v3.0
Integração profissional com Sonarr para tradução automática de legendas
"""

import warnings
warnings.filterwarnings("ignore", message="Core Pydantic V1 functionality")

from modules.gui_sonarr import run_sonarr_gui

if __name__ == '__main__':
    run_sonarr_gui()