# services/__init__.py
# ─────────────────────────────────────────────────────────────────────────────
# インフラ層パッケージ。
#
# 依存の方向（厳守）:
#   main.py / agents/*  →  services/*  →  外部API / DB
#
# services/* 内で agents/* を import することは禁止（循環インポート防止）。
# services 間の依存は以下の一方向のみ許可:
#   emotion.py   → state.py, location.py
#   memory.py    → state.py
#   scheduler.py → state.py, tts.py, emotion.py, memory.py
#   snap.py      → (依存なし)
#   persona.py   → (依存なし)
#   tts.py       → (依存なし)
#   location.py  → (依存なし)
#   state.py     → (依存なし) ← 循環の起点にならない最下層
# ─────────────────────────────────────────────────────────────────────────────
