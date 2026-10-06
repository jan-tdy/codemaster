"""Jadiv Code Master v2 – a universal desktop app store.

Submodules:
  constants       – app identity, on-disk paths, static lookup tables
  persistence      – config/installed/catalog-cache JSON I/O
  icons            – icon pixmap helpers
  desktop_entry    – .desktop launcher helpers
  git_worker       – clone/update/install-deps for git-backed apps
  system_packages  – apt/snap/flatpak search, install, remove, launch
  catalog_loader   – discovers git apps (jan-tdy scan + community catalog)
  widgets          – tile grid, sidebar, details page UI components
  main_window      – the CodeMaster QMainWindow wiring everything together
"""
